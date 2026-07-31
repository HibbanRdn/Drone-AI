from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .models import UltralyticsModel, select_device
from .schema import FrameResult, ModelOutput, Telemetry
from .storage import SessionWriter
from .tiling import predict_tiled_detector


class InferenceRuntime:
    def __init__(self, config: Dict[str, Any], *, backend: str = "auto"):
        self.config = config
        self.backend = backend
        self.status = "Initializing"
        self.running = True
        self.plant_enabled = bool(config["models"]["detector"]["enabled"])
        self.segmenter_enabled = bool(config["models"]["segmenter"]["enabled"])
        self._closed = False
        self._last_segmentation: Optional[ModelOutput] = None
        self._last_result: Optional[FrameResult] = None
        self._started_perf = time.perf_counter()
        self._processed = 0
        device = select_device(str(config["runtime"]["device"]))
        self.detector = UltralyticsModel(
            config["models"]["detector"], backend=backend, device=device
        )
        self.segmenter = UltralyticsModel(
            config["models"]["segmenter"], backend=backend, device=device
        )
        model_audit = {
            "detector": self.detector.audit(),
            "segmenter": self.segmenter.audit(),
        }
        self.writer = SessionWriter(
            config["runtime"]["root"],
            config["storage"],
            {
                "application": config["app"],
                "camera": config["camera"],
                "backend": backend,
                "device": str(device),
                "models": model_audit,
                "config_path": config.get("_config_path"),
            },
        )
        self.status = "Ready"
        self.writer.logger.info(
            "runtime_ready detector=%s segmenter=%s backend=%s",
            self.detector.path.name,
            self.segmenter.path.name,
            backend,
        )

    @property
    def session_id(self) -> str:
        return self.writer.session_id

    def set_controls(
        self,
        *,
        running: Optional[bool] = None,
        plant_enabled: Optional[bool] = None,
        segmenter_enabled: Optional[bool] = None,
    ) -> None:
        if running is not None:
            self.running = bool(running)
        if plant_enabled is not None:
            self.plant_enabled = bool(plant_enabled)
        if segmenter_enabled is not None:
            self.segmenter_enabled = bool(segmenter_enabled)
        self.status = "Running" if self.running else "Ready"

    def _predict_detector(self, frame_bgr: np.ndarray) -> ModelOutput:
        detector_config = self.config["models"]["detector"]
        if not bool(detector_config.get("tiled", False)):
            return self.detector.predict(frame_bgr)
        return predict_tiled_detector(self.detector, frame_bgr, detector_config)

    def process_frame(
        self,
        frame_bgr: np.ndarray,
        *,
        frame_index: int,
        capture_timestamp: str,
        camera_source: str,
        telemetry: Optional[Telemetry] = None,
        snapshot: bool = False,
    ) -> Tuple[FrameResult, np.ndarray]:
        if self._closed:
            raise RuntimeError("runtime sudah ditutup")
        if frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3 or frame_bgr.size == 0:
            raise ValueError("frame harus HxWx3 non-empty")
        telemetry = telemetry or Telemetry()
        started = time.perf_counter()
        warning: List[str] = []
        detector_output = ModelOutput()
        segment_output = ModelOutput()
        detector_ran = False
        segmenter_ran = False
        segmenter_reused = False

        if self.running:
            self.status = "Running"
            detector_interval = int(self.config["models"]["detector"]["interval_frames"])
            segment_interval = int(self.config["models"]["segmenter"]["interval_frames"])
            if self.plant_enabled and frame_index % detector_interval == 0:
                detector_ran = True
                detector_output = self._predict_detector(frame_bgr)
                if detector_output.warning:
                    warning.append(f"detector:{detector_output.warning}")
            if self.segmenter_enabled and frame_index % segment_interval == 0:
                segmenter_ran = True
                segment_output = self.segmenter.predict(frame_bgr)
                if segment_output.warning:
                    warning.append(f"segmenter:{segment_output.warning}")
                else:
                    self._last_segmentation = segment_output
            elif (
                self.segmenter_enabled
                and bool(self.config["runtime"].get("reuse_last_segmentation", True))
                and self._last_segmentation is not None
            ):
                segment_output = self._last_segmentation
                segmenter_reused = True
        else:
            self.status = "Ready"

        inference_timestamp = datetime.now(timezone.utc).isoformat()
        total_ms = (time.perf_counter() - started) * 1000
        elapsed = max(time.perf_counter() - self._started_perf, 1e-9)
        self._processed += 1
        fps = self._processed / elapsed
        contours = segment_output.contours if self.segmenter_enabled else []
        result = FrameResult(
            session_id=self.session_id,
            frame_index=frame_index,
            capture_timestamp=capture_timestamp,
            inference_timestamp=inference_timestamp,
            camera_source=camera_source,
            image_width=int(frame_bgr.shape[1]),
            image_height=int(frame_bgr.shape[0]),
            plant_detections=detector_output.detections if self.plant_enabled else [],
            plot_segmentation={
                "representation": "simplified_contours_pixels",
                "contours": contours,
                "contour_count": len(contours),
                "reused_from_previous_frame": self.segmenter_enabled and not segmenter_ran,
            }
            if self.segmenter_enabled
            else None,
            model_version={
                "detector": self.detector.path.name,
                "segmenter": self.segmenter.path.name,
            },
            model_sha256={
                "detector": self.detector.sha256,
                "segmenter": self.segmenter.sha256,
            },
            confidence_threshold={
                "detector": float(
                    self.config["models"]["detector"]["confidence_threshold"]
                ),
                "segmenter": float(
                    self.config["models"]["segmenter"]["confidence_threshold"]
                ),
            },
            inference_latency_ms=total_ms,
            aircraft_latitude=telemetry.aircraft_latitude,
            aircraft_longitude=telemetry.aircraft_longitude,
            relative_altitude=telemetry.relative_altitude,
            absolute_altitude=telemetry.absolute_altitude,
            gimbal_pitch=telemetry.gimbal_pitch,
            aircraft_heading=telemetry.aircraft_heading,
            rtk_status=telemetry.rtk_status,
            warning=warning,
            metrics={
                "fps_session_average": fps,
                "total_latency_ms": total_ms,
                "detector_ran": detector_ran,
                "segmenter_ran": segmenter_ran,
                "detector_latency_ms": (
                    detector_output.latency_ms if detector_ran else 0.0
                ),
                "segmenter_latency_ms": (
                    segment_output.latency_ms if segmenter_ran else 0.0
                ),
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
                "plant_count_frame": len(detector_output.detections),
                "plot_contour_count_frame": len(contours),
                "warning_count": len(warning),
                "detector_tiling": detector_output.diagnostics,
            },
        )
        overlay = draw_overlay(frame_bgr, result, self.config["overlay"], self.status)
        self.writer.append(result, telemetry)
        if snapshot and bool(self.config["storage"]["save_snapshots"]):
            self.writer.save_snapshot(overlay, frame_index)
        self._last_result = result
        return result, overlay

    def close(self, status: str = "stopped") -> Path:
        if not self._closed:
            self._closed = True
            self.status = "Ready" if status == "stopped" else "Error"
        return self.writer.close(status)


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
    for detection in result.plant_detections:
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
            label = (
                f"{detection.get('class_name', 'plant')} "
                f"{float(detection.get('confidence', 0)):.2f}"
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
        fps = float(result.metrics.get("fps_session_average", 0))
        latency = float(result.metrics.get("total_latency_ms", 0))
        text = (
            f"AI {status} | plants(frame)={len(result.plant_detections)} "
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
