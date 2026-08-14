from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .models import UltralyticsModel, select_device
from .overlay import select_overlay_detections
from .schema import FrameResult, ModelOutput, Telemetry
from .state import AppState, StateMachine
from .storage import SessionWriter
from .tiling import predict_tiled_detector
from .tensorrt_backend import DirectTensorRTModel, DirectTensorRTSegmenterModel


class InferenceRuntime:
    """Persistent model lifecycle shared by live and offline frontends.

    Model objects survive Start/Stop cycles. A new bounded session writer is
    created for each Start, while the direct TensorRT or host-only Ultralytics
    backend is loaded and warmed only once per worker process.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        backend: str = "auto",
        model_factory: Optional[Callable[..., Any]] = None,
        state_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.config = config
        self.backend = backend
        self._model_factory = model_factory or (
            DirectTensorRTModel if backend == "engine" else UltralyticsModel
        )
        self.state_machine = StateMachine(state_callback)
        self.detector: Optional[Any] = None
        self.segmenter: Optional[Any] = None
        self.writer: Optional[SessionWriter] = None
        self.plant_enabled = bool(config["models"]["detector"]["enabled"])
        self.segmenter_enabled = bool(config["models"]["segmenter"]["enabled"])
        self._models_initialized = False
        self._warmed_up = False
        self._closed = False
        self._last_segmentation: Optional[ModelOutput] = None
        self._last_result: Optional[FrameResult] = None
        self._last_summary: Optional[Path] = None
        self._session_started_perf = 0.0
        self._processed = 0
        self._latency_window: Deque[float] = deque(maxlen=120)
        self._lifecycle_lock = threading.RLock()
        self._inference_lock = threading.Lock()
        self._model_load_count = 0
        self._warmup_count = 0
        self._device: Optional[Any] = None

    @property
    def status(self) -> str:
        return self.state_machine.state.value

    @property
    def running(self) -> bool:
        return self.state_machine.state == AppState.RUNNING

    @property
    def session_id(self) -> Optional[str]:
        return self.writer.session_id if self.writer is not None else None

    @property
    def model_load_count(self) -> int:
        return self._model_load_count

    @property
    def warmup_count(self) -> int:
        return self._warmup_count

    @property
    def backend_initialization_count(self) -> int:
        return sum(
            int(getattr(model, "_backend_initialization_count", 0))
            for model in (self.detector, self.segmenter)
            if model is not None
        )

    def initialize(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("runtime sudah di-shutdown")
            if self._models_initialized:
                return
            self._device = (
                "cuda:0"
                if self.backend == "engine"
                else select_device(str(self.config["runtime"]["device"]))
            )
            detector_config = self.config["models"]["detector"]
            segmenter_config = self.config["models"]["segmenter"]
            detector = None
            segmenter = None
            if bool(detector_config["enabled"]):
                detector = DirectTensorRTModel(
                    detector_config, backend=self.backend, device=self._device
                )
            if bool(segmenter_config["enabled"]):
                segmenter = DirectTensorRTSegmenterModel(
                    segmenter_config, backend=self.backend, device=self._device
                )
            if detector is None and segmenter is None:
                raise RuntimeError("tidak ada model inference yang diaktifkan")
            self.detector = detector
            self.segmenter = segmenter
            self._model_load_count += int(detector is not None) + int(segmenter is not None)
            self._models_initialized = True

    def warmup(self) -> None:
        with self._lifecycle_lock:
            if self._warmed_up:
                return
            if not self._models_initialized:
                self.initialize()
            for model in (self.detector, self.segmenter):
                if model is None:
                    continue
                if hasattr(model, "warmup"):
                    model.warmup()
                else:
                    image_size = int(model.config["image_size"])
                    output = model.predict(
                        np.zeros((image_size, image_size, 3), dtype=np.uint8)
                    )
                    if output.warning:
                        raise RuntimeError(f"Model warm-up gagal: {output.warning}")
            self._warmed_up = True
            self._warmup_count += 1

    def _model_audit(self, model: Optional[Any]) -> Dict[str, Any]:
        if model is None:
            return {"enabled": False}
        return {"enabled": True, **model.audit()}

    def _open_session(self) -> None:
        if self.writer is not None:
            return
        storage_config = {
            **self.config["storage"],
            **self.config.get("logging", {}),
        }
        self.writer = SessionWriter(
            self.config["runtime"]["root"],
            storage_config,
            {
                "schema_version": "1.0",
                "application": self.config["app"],
                "camera": self.config["camera"],
                "live": self.config.get("live"),
                "backend": self.backend,
                "device": str(self._device),
                "models": {
                    "detector": self._model_audit(self.detector),
                    "segmenter": self._model_audit(self.segmenter),
                },
                "model_load_count": self._model_load_count,
                "config_path": self.config.get("_config_path"),
            },
        )
        self._session_started_perf = time.perf_counter()
        self._session_start_ts = time.perf_counter()
        self._first_valid_frame = False
        self._processed = 0
        self._latency_window.clear()
        self._last_segmentation = None

    def _close_session(self, status: str) -> Optional[Path]:
        if self.writer is None:
            return self._last_summary
        self._last_summary = self.writer.close(
            status,
            {
                "model_load_count": self._model_load_count,
                "warmup_count": self._warmup_count,
                "backend_initialization_count": self.backend_initialization_count,
            },
        )
        self.writer = None
        return self._last_summary

    def start(self) -> bool:
        with self._lifecycle_lock:
            state = self.state_machine.state
            if state in {AppState.STARTING, AppState.WARMING_UP, AppState.RUNNING}:
                return False
            if state == AppState.STOPPING:
                raise RuntimeError("Start ditolak saat STOPPING")
            self.state_machine.transition(AppState.STARTING)
            try:
                self.initialize()
                self._open_session()
                live_config = self.config.get("live", {})
                if bool(live_config.get("warmup_before_running", True)) and not self._warmed_up:
                    self.state_machine.transition(AppState.WARMING_UP)
                    self.warmup()
                self.state_machine.transition(AppState.RUNNING)
                if self.writer is not None:
                    self.writer.logger.info(
                        "runtime_running model_load_count=%d warmup_count=%d backend=%s",
                        self._model_load_count,
                        self._warmup_count,
                        self.backend,
                    )
                return True
            except Exception as exc:
                if self.writer is not None:
                    self.writer.logger.exception("runtime_start_failed")
                    self._close_session("error")
                self.state_machine.fail(
                    "START_FAILED", f"{type(exc).__name__}: {exc}", recoverable=True
                )
                raise

    def stop(self, status: str = "stopped") -> Optional[Path]:
        with self._inference_lock:
            # v22: capture session_dir before closing writer
            session_dir = self.writer.session_dir if self.writer is not None else None
            config_snapshot = dict(self.config) if self.config else {}

            with self._lifecycle_lock:
                state = self.state_machine.state
                if state == AppState.IDLE:
                    return self._last_summary
                if state != AppState.STOPPING:
                    self.state_machine.transition(AppState.STOPPING)
                if self.writer is not None:
                    self._close_session(status)
                self._last_segmentation = None
                self._last_result = None
                self.state_machine.transition(AppState.FINALIZING)
                summary = self._last_summary

            # v22: run finalizer after session close (Stop AI path)
            if session_dir is not None and Path(session_dir).exists():
                try:
                    import sys
                    print("[V22] finalizer_started session=%s" % Path(session_dir).name, file=sys.stderr)
                    from .finalizer import finalize_session
                    result = finalize_session(session_dir, config_snapshot)
                    print("[V22] finalizer_complete status=%s plants=%d gaps=%s" % (
                        result.status, result.plants_count,
                        str(result.gaps_count) if result.gap_evaluated else "N/A"), file=sys.stderr)
                except Exception:
                    print("[V22] finalizer_failed", file=sys.stderr)
                    import traceback; traceback.print_exc()

            self.state_machine.transition(AppState.IDLE)
            return summary

    def fail(self, code: str, message: str, *, session_status: str = "error") -> None:
        with self._inference_lock:
            with self._lifecycle_lock:
                if self.writer is not None:
                    self.writer.logger.error("%s: %s", code, message)
                    self._close_session(session_status)
                self._last_segmentation = None
                self._last_result = None
                self.state_machine.fail(code, message, recoverable=True)

    def set_controls(
        self,
        *,
        running: Optional[bool] = None,
        plant_enabled: Optional[bool] = None,
        segmenter_enabled: Optional[bool] = None,
    ) -> None:
        if plant_enabled is not None:
            self.plant_enabled = bool(plant_enabled) and self.detector is not None
        if segmenter_enabled is not None:
            self.segmenter_enabled = bool(segmenter_enabled) and self.segmenter is not None
        if running is True:
            self.start()
        elif running is False:
            self.stop()

    def _predict_detector(self, frame_bgr: np.ndarray) -> ModelOutput:
        if self.detector is None:
            return ModelOutput()
        detector_config = self.config["models"]["detector"]
        if not bool(detector_config.get("tiled", False)):
            return self.detector.predict(frame_bgr)
        return predict_tiled_detector(self.detector, frame_bgr, detector_config)

    @staticmethod
    def _fatal_model_warning(warning: Optional[str]) -> bool:
        if not warning:
            return False
        lowered = warning.lower()
        return any(
            marker in lowered
            for marker in ("cuda", "tensorrt", "out of memory", "engine deserial")
        )

    def infer_frame(
        self,
        frame_bgr: np.ndarray,
        *,
        frame_index: int,
        capture_timestamp: str,
        camera_source: str,
        telemetry: Optional[Telemetry] = None,
        snapshot: bool = False,
        source_frame_id: Optional[int] = None,
        capture_monotonic_ns: Optional[int] = None,
        row_stride: Optional[int] = None,
        pixel_format: Optional[str] = None,
    ) -> Tuple[FrameResult, np.ndarray]:
        return self.process_frame(
            frame_bgr,
            frame_index=frame_index,
            capture_timestamp=capture_timestamp,
            camera_source=camera_source,
            telemetry=telemetry,
            snapshot=snapshot,
            source_frame_id=source_frame_id,
            capture_monotonic_ns=capture_monotonic_ns,
            row_stride=row_stride,
            pixel_format=pixel_format,
        )

    def process_frame(
        self,
        frame_bgr: np.ndarray,
        *,
        frame_index: int,
        capture_timestamp: str,
        camera_source: str,
        telemetry: Optional[Telemetry] = None,
        snapshot: bool = False,
        source_frame_id: Optional[int] = None,
        capture_monotonic_ns: Optional[int] = None,
        row_stride: Optional[int] = None,
        pixel_format: Optional[str] = None,
    ) -> Tuple[FrameResult, np.ndarray]:
        if self._closed:
            raise RuntimeError("runtime sudah di-shutdown")
        if frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3 or frame_bgr.size == 0:
            raise ValueError("frame harus HxWx3 non-empty")
        if self.state_machine.state == AppState.IDLE:
            self.start()
        if self.state_machine.state != AppState.RUNNING or self.writer is None:
            raise RuntimeError(f"inference ditolak pada state {self.status}")

        with self._inference_lock:
            telemetry = telemetry or Telemetry()
            started = time.perf_counter()
            warning: List[str] = []
            detector_output = ModelOutput()
            segment_output = ModelOutput()
            detector_ran = False
            segmenter_ran = False
            segmenter_reused = False
            detector_interval = int(self.config["models"]["detector"]["interval_frames"])
            segment_interval = int(self.config["models"]["segmenter"]["interval_frames"])

            try:
                if self.plant_enabled and frame_index % detector_interval == 0:
                    detector_ran = True
                    detector_output = self._predict_detector(frame_bgr)
                    if detector_output.warning:
                        warning.append(f"detector:{detector_output.warning}")
                    if self._fatal_model_warning(detector_output.warning):
                        raise RuntimeError(detector_output.warning)
                if (
                    self.segmenter_enabled
                    and self.segmenter is not None
                    and frame_index % segment_interval == 0
                ):
                    segmenter_ran = True
                    segment_output = self.segmenter.predict(frame_bgr)
                    if segment_output.warning:
                        warning.append(f"segmenter:{segment_output.warning}")
                    if self._fatal_model_warning(segment_output.warning):
                        raise RuntimeError(segment_output.warning)
                    if not segment_output.warning:
                        self._last_segmentation = segment_output
                elif (
                    self.segmenter_enabled
                    and bool(self.config["runtime"].get("reuse_last_segmentation", True))
                    and self._last_segmentation is not None
                ):
                    segment_output = self._last_segmentation
                    segmenter_reused = True
            except Exception as exc:
                self.writer.logger.exception("inference_failed frame=%d", frame_index)
                self.state_machine.fail(
                    "INFERENCE_FAILED", f"{type(exc).__name__}: {exc}", recoverable=True
                )
                raise

            inference_ms = (time.perf_counter() - started) * 1000
            detections = detector_output.detections if self.plant_enabled else []
            overlay_config = self.config["overlay"]
            configured_min_confidence = overlay_config.get("min_confidence", "existing")
            min_confidence = (
                float(self.config["models"]["detector"]["confidence_threshold"])
                if configured_min_confidence == "existing"
                else float(configured_min_confidence)
            )
            selection = select_overlay_detections(
                detections,
                max_objects=overlay_config["max_objects"],
                min_confidence=min_confidence,
                frame_width=int(frame_bgr.shape[1]),
                frame_height=int(frame_bgr.shape[0]),
                strategy=str(overlay_config["selection_strategy"]),
            )
            contours = segment_output.contours if self.segmenter_enabled else []
            self._processed += 1
            if not self._first_valid_frame:
                self._first_valid_frame = True
                self._session_start_perf_fps = time.perf_counter()
                self._processed_fps = 0
            self._processed_fps = self._processed_fps + 1 if hasattr(self, '_processed_fps') else 1
            if not hasattr(self, '_session_start_perf_fps'):
                self._session_start_perf_fps = self._session_started_perf
            elapsed_fps = max(time.perf_counter() - self._session_start_perf_fps, 1e-9)
            ai_fps = self._processed_fps / elapsed_fps
            model_version = {
                "detector": self.detector.path.name if self.detector is not None else "disabled",
                "segmenter": self.segmenter.path.name if self.segmenter is not None else "disabled",
            }
            model_sha256 = {
                "detector": self.detector.sha256 if self.detector is not None else "",
                "segmenter": self.segmenter.sha256 if self.segmenter is not None else "",
            }
            result = FrameResult(
                session_id=self.writer.session_id,
                frame_index=frame_index,
                capture_timestamp=capture_timestamp,
                inference_timestamp=datetime.now(timezone.utc).isoformat(),
                camera_source=camera_source,
                image_width=int(frame_bgr.shape[1]),
                image_height=int(frame_bgr.shape[0]),
                plant_detections=detections,
                plot_segmentation={
                    "representation": "simplified_contours_pixels",
                    "contours": contours,
                    "contour_count": len(contours),
                    "reused_from_previous_frame": segmenter_reused,
                }
                if self.segmenter_enabled
                else None,
                model_version=model_version,
                model_sha256=model_sha256,
                confidence_threshold={
                    "detector": float(
                        self.config["models"]["detector"]["confidence_threshold"]
                    ),
                    "segmenter": float(
                        self.config["models"]["segmenter"]["confidence_threshold"]
                    ),
                },
                inference_latency_ms=inference_ms,
                aircraft_latitude=telemetry.aircraft_latitude if (telemetry.gps_signal_level or 0) > 0 else None,
                aircraft_longitude=telemetry.aircraft_longitude if (telemetry.gps_signal_level or 0) > 0 else None,
                relative_altitude=telemetry.relative_altitude,
                absolute_altitude=telemetry.absolute_altitude,
                gimbal_pitch=telemetry.gimbal_pitch,
                aircraft_heading=telemetry.aircraft_heading,
                rtk_status=telemetry.rtk_status,
                warning=warning,
                source_frame_id=source_frame_id,
                capture_monotonic_ns=capture_monotonic_ns,
                row_stride=row_stride or int(frame_bgr.strides[0]),
                pixel_format=pixel_format or "BGR_PACKED",
                gap_candidates=None,
                gap_status={
                    "enabled": False,
                    "ran": False,
                    "status": "pending_post_session",
                    "skip_reason": "evaluated_during_finalization",
                    "candidate_count": 0
                },
                overlay={
                    "enabled": bool(overlay_config["enabled"]),
                    "aspect_mode": str(overlay_config["aspect_mode"]),
                    "rotation_degrees": int(overlay_config["rotation_degrees"]),
                    "total_detections": selection.total_detections,
                    "eligible_detections": selection.eligible_detections,
                    "sent_objects": selection.sent_objects,
                    "truncated_objects": selection.truncated_objects,
                    "api_limit": selection.api_limit,
                    "strategy": selection.strategy,
                    "detections": selection.detections,
                },
                metrics={
                    "fps_session_average": ai_fps,
                    "ai_fps": ai_fps,
                    "detector_ran": detector_ran,
                    "segmenter_ran": segmenter_ran,
                    "detector_latency_ms": detector_output.latency_ms if detector_ran else 0.0,
                    "segmenter_latency_ms": segment_output.latency_ms if segmenter_ran else 0.0,
                    "segmenter_cached_output_latency_ms": (
                        segment_output.latency_ms if segmenter_reused else None
                    ),
                    "segmenter_reused": segmenter_reused,
                    "detector_preprocessing_ms": (
                        detector_output.preprocessing_ms if detector_ran else None
                    ),
                    "detector_inference_ms": (
                        detector_output.inference_ms if detector_ran else None
                    ),
                    "detector_postprocessing_ms": (
                        detector_output.postprocessing_ms if detector_ran else None
                    ),
                    "segmenter_preprocessing_ms": (
                        segment_output.preprocessing_ms if segmenter_ran else None
                    ),
                    "segmenter_inference_ms": (
                        segment_output.inference_ms if segmenter_ran else None
                    ),
                    "segmenter_postprocessing_ms": (
                        segment_output.postprocessing_ms if segmenter_ran else None
                    ),
                    "plant_count_frame": len(detections),
                    "gap_candidate_count_frame": None,
                    "plot_contour_count_frame": len(contours),
                    "warning_count": len(warning),
                    "detector_tiling": detector_output.diagnostics,
                    "global_nms_ms": detector_output.diagnostics.get(
                        "global_nms_ms"
                    ),
                    "overlay_total_detections": selection.total_detections,
                    "overlay_sent_objects": selection.sent_objects,
                    "overlay_truncated_objects": selection.truncated_objects,
                    "backend_initialization_count": self.backend_initialization_count,
                },
            )
            overlay_started = time.perf_counter()
            overlay = draw_overlay(frame_bgr, result, overlay_config, self.status)
            overlay_ms = (time.perf_counter() - overlay_started) * 1000
            total_ms = (time.perf_counter() - started) * 1000
            self._latency_window.append(total_ms)
            result.inference_latency_ms = total_ms
            result.metrics["inference_pipeline_ms"] = inference_ms
            result.metrics["overlay_latency_ms"] = overlay_ms
            result.metrics["total_latency_ms"] = total_ms
            result.metrics["p50_latency_ms"] = median(self._latency_window)
            self.writer.append(result, telemetry)
            if snapshot and bool(self.config["storage"]["save_snapshots"]):
                self.writer.save_snapshot(overlay, frame_index)
            self.writer.save_sample(frame_bgr, overlay, frame_index)
            self._last_result = result
            return result, overlay

    def shutdown(self, status: str = "stopped") -> Optional[Path]:
        with self._lifecycle_lock:
            if self._closed:
                return self._last_summary
        if self.state_machine.state != AppState.IDLE:
            self.stop(status)
        with self._lifecycle_lock:
            for model in (self.detector, self.segmenter):
                if model is not None and hasattr(model, "shutdown"):
                    model.shutdown()
            self.detector = None
            self.segmenter = None
            self._closed = True
            return self._last_summary

    def close(self, status: str = "stopped") -> Optional[Path]:
        return self.shutdown(status)


def draw_overlay(
    frame_bgr: np.ndarray,
    result: FrameResult,
    overlay_config: Dict[str, Any],
    status: str,
) -> np.ndarray:
    output = frame_bgr.copy()
    line_width = int(overlay_config["line_width"])
    detector_color = tuple(int(value) for value in overlay_config["detector_bgr"])
    segment_color = tuple(int(value) for value in overlay_config["segmenter_bgr"])
    status_color = tuple(int(value) for value in overlay_config["status_bgr"])
    height, width = output.shape[:2]
    overlay_detections = (
        result.overlay.get("detections", result.plant_detections)
        if bool(overlay_config.get("enabled", True))
        else []
    )
    for detection in overlay_detections:
        values = detection.get("bbox_xyxy", [])
        if len(values) != 4:
            continue
        x1, y1, x2, y2 = (
            int(np.clip(values[0], 0, width - 1)),
            int(np.clip(values[1], 0, height - 1)),
            int(np.clip(values[2], 0, width - 1)),
            int(np.clip(values[3], 0, height - 1)),
        )
        if x2 <= x1 or y2 <= y1:
            continue
        cv2.rectangle(output, (x1, y1), (x2, y2), detector_color, line_width)
        if bool(overlay_config.get("show_labels", True)):
            label = "{} {:.2f}".format(
                detection.get("class_name", "plant"),
                float(detection.get("confidence", 0)),
            )
            cv2.putText(
                output,
                label,
                (x1, max(14, y1 - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                detector_color,
                1,
                cv2.LINE_AA,
            )
    segmentation = result.plot_segmentation or {}
    for contour in segmentation.get("contours", []):
        points = np.rint(np.asarray(contour, dtype=np.float32)).astype(np.int32)
        if points.ndim == 2 and len(points) >= 3:
            cv2.polylines(output, [points], True, segment_color, line_width, cv2.LINE_AA)
    if bool(overlay_config.get("show_status", True)):
        fps = float(result.metrics.get("ai_fps", 0))
        latency = float(result.metrics.get("total_latency_ms", result.inference_latency_ms))
        text = (
            f"AI {status} | plants={len(result.plant_detections)} "
            f"| {fps:.1f} FPS | {latency:.0f} ms"
        )
        cv2.putText(
            output,
            text,
            (12, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            status_color,
            2,
            cv2.LINE_AA,
        )
    return output
