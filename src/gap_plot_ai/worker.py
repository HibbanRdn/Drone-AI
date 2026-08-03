from __future__ import annotations

import argparse
import json
import math
import os
import signal
import struct
import time
from pathlib import Path
from typing import Any, Dict, Optional

import cv2
import numpy as np

from .config import load_config
from .overlay import CoordinateMapper
from .runtime import InferenceRuntime
from .schema import Telemetry, json_safe
from .sources import FrameEnvelope, FrameMetadata
from .state import AppState

MAGIC = b"GPAIFRM2"
FRAME_HEADER_VERSION = 2
FRAME_HEADER = struct.Struct("<8sHHQIQQQIIIII14diiII")
TELEMETRY_LAT = 1 << 0
TELEMETRY_LON = 1 << 1
TELEMETRY_REL_ALT = 1 << 2
TELEMETRY_ABS_ALT = 1 << 3
TELEMETRY_GIMBAL = 1 << 4
TELEMETRY_ATTITUDE = 1 << 5
TELEMETRY_RTK = 1 << 6
TELEMETRY_VELOCITY = 1 << 7
TELEMETRY_GPS_QUALITY = 1 << 8


def _atomic_text(path: Path, value: str) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(value, encoding="utf-8")
    os.replace(temp, path)


def _read_control(path: Path) -> Dict[str, int]:
    control = {"running": 0, "plant": 1, "segmenter": 0, "snapshot_seq": 0}
    if not path.is_file():
        return control
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return control
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip() in control:
            try:
                control[key.strip()] = int(value.strip())
            except ValueError:
                continue
    return control


def read_frame(path: Path) -> FrameEnvelope:
    payload = path.read_bytes()
    if len(payload) < FRAME_HEADER.size:
        raise ValueError("frame spool lebih pendek dari header v2")
    unpacked = FRAME_HEADER.unpack_from(payload)
    (
        magic,
        header_version,
        pixel_format,
        sequence,
        source_frame_id,
        capture_monotonic_ns,
        capture_wall_ns,
        telemetry_monotonic_ns,
        width,
        height,
        row_stride,
        channels,
        data_len,
        latitude,
        longitude,
        relative_altitude,
        absolute_altitude,
        aircraft_roll,
        aircraft_pitch,
        aircraft_yaw,
        gimbal_roll,
        gimbal_pitch,
        gimbal_yaw,
        velocity_x,
        velocity_y,
        velocity_z,
        speed_mps,
        rtk_status,
        gps_signal_level,
        visible_satellites,
        valid_mask,
    ) = unpacked
    if magic != MAGIC or header_version != FRAME_HEADER_VERSION or channels != 3:
        raise ValueError("magic/version/pixel channels spool tidak valid")
    packed_stride = int(width) * int(channels)
    if row_stride != packed_stride:
        raise ValueError(
            f"row stride belum didukung: diterima={row_stride}, packed={packed_stride}"
        )
    expected = int(row_stride) * int(height)
    if data_len != expected or len(payload) != FRAME_HEADER.size + expected:
        raise ValueError("ukuran RGB spool tidak konsisten")
    rgb = np.frombuffer(payload, dtype=np.uint8, offset=FRAME_HEADER.size)
    rgb = rgb.reshape((height, width, channels))
    frame_bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    pixel_format_name = (
        "PIXFMT_RGB_PACKED" if int(pixel_format) == 5 else f"PSDK_PIXFMT_{pixel_format}"
    )
    telemetry = Telemetry(
        aircraft_latitude=latitude if valid_mask & TELEMETRY_LAT else None,
        aircraft_longitude=longitude if valid_mask & TELEMETRY_LON else None,
        relative_altitude=relative_altitude if valid_mask & TELEMETRY_REL_ALT else None,
        absolute_altitude=absolute_altitude if valid_mask & TELEMETRY_ABS_ALT else None,
        aircraft_roll=aircraft_roll if valid_mask & TELEMETRY_ATTITUDE else None,
        aircraft_pitch=aircraft_pitch if valid_mask & TELEMETRY_ATTITUDE else None,
        aircraft_yaw=aircraft_yaw if valid_mask & TELEMETRY_ATTITUDE else None,
        aircraft_heading=aircraft_yaw if valid_mask & TELEMETRY_ATTITUDE else None,
        gimbal_roll=gimbal_roll if valid_mask & TELEMETRY_GIMBAL else None,
        gimbal_pitch=gimbal_pitch if valid_mask & TELEMETRY_GIMBAL else None,
        gimbal_yaw=gimbal_yaw if valid_mask & TELEMETRY_GIMBAL else None,
        velocity_x=velocity_x if valid_mask & TELEMETRY_VELOCITY else None,
        velocity_y=velocity_y if valid_mask & TELEMETRY_VELOCITY else None,
        velocity_z=velocity_z if valid_mask & TELEMETRY_VELOCITY else None,
        speed_mps=speed_mps if valid_mask & TELEMETRY_VELOCITY else None,
        gps_signal_level=(
            gps_signal_level
            if valid_mask & TELEMETRY_GPS_QUALITY and gps_signal_level >= 0
            else None
        ),
        visible_satellites=(
            visible_satellites if valid_mask & TELEMETRY_GPS_QUALITY else None
        ),
        rtk_status=rtk_status if valid_mask & TELEMETRY_RTK else None,
        source_timestamp=FrameMetadata(
            sequence=int(sequence),
            source_frame_id=int(source_frame_id),
            capture_monotonic_ns=int(capture_monotonic_ns),
            capture_wall_clock_ns=int(capture_wall_ns),
            width=int(width),
            height=int(height),
            row_stride=int(row_stride),
            pixel_format=pixel_format_name,
            camera_source="DJI_LIVEVIEW_CAMERA_SOURCE_M4E_VIS",
        ).capture_timestamp,
        source_monotonic_ns=(
            int(telemetry_monotonic_ns) if telemetry_monotonic_ns else None
        ),
    )
    metadata = FrameMetadata(
        sequence=int(sequence),
        source_frame_id=int(source_frame_id),
        capture_monotonic_ns=int(capture_monotonic_ns),
        capture_wall_clock_ns=int(capture_wall_ns),
        width=int(width),
        height=int(height),
        row_stride=int(row_stride),
        pixel_format=pixel_format_name,
        camera_source="DJI_LIVEVIEW_CAMERA_SOURCE_M4E_VIS",
    )
    return FrameEnvelope(frame_bgr=frame_bgr, metadata=metadata, telemetry=telemetry)


def serialize_result(result: Any, capture_monotonic_ns: int, status: str) -> str:
    fps = float(result.metrics.get("ai_fps", 0))
    p50 = float(result.metrics.get("p50_latency_ms", result.inference_latency_ms))
    overlay = result.overlay or {}
    selected = (
        overlay.get("detections", result.plant_detections)
        if overlay.get("enabled", True)
        else []
    )
    gap_count = len(result.gap_candidates) if result.gap_candidates is not None else -1
    lines = [
        (
            f"RESULT {result.frame_index} {capture_monotonic_ns} {time.monotonic_ns()} "
            f"{result.inference_latency_ms:.6f} {fps:.6f} {p50:.6f} {status} "
            f"{len(result.warning)} {len(result.plant_detections)} {len(selected)} "
            f"{gap_count} {result.image_width} {result.image_height} "
            f"{int(result.row_stride or result.image_width * 3)}"
        )
    ]
    mapper = CoordinateMapper(
        result.image_width,
        result.image_height,
        aspect_mode=str(overlay.get("aspect_mode", "stretch")),
        rotation_degrees=int(overlay.get("rotation_degrees", 0)),
    )
    for detection_id, detection in enumerate(selected):
        box = detection.get("bbox_xyxy", [])
        if len(box) != 4:
            continue
        try:
            x1, y1, x2, y2 = mapper.map_box(box)
        except ValueError:
            continue
        if x2 <= x1 or y2 <= y1:
            continue
        confidence = float(detection.get("confidence", 0))
        if not math.isfinite(confidence):
            continue
        lines.append(
            "BOX "
            f"{detection_id} {int(detection.get('class_id', 0))} "
            f"{confidence:.8f} {x1 / 10000:.8f} {y1 / 10000:.8f} "
            f"{x2 / 10000:.8f} {y2 / 10000:.8f}"
        )
    segmentation = result.plot_segmentation or {}
    for contour_id, contour in enumerate(segmentation.get("contours", [])):
        values = []
        for x, y in contour:
            values.extend(
                (
                    f"{float(x) / max(result.image_width, 1):.8f}",
                    f"{float(y) / max(result.image_height, 1):.8f}",
                )
            )
        lines.append(f"CONTOUR {contour_id} {len(contour)} " + " ".join(values))
    lines.append("END")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gap Plot AI persistent Manifold worker")
    parser.add_argument("--config", required=True)
    parser.add_argument("--ipc-dir", required=True)
    parser.add_argument(
        "--backend", choices=("auto", "pt", "onnx", "engine"), default="engine"
    )
    return parser


def run(args: argparse.Namespace) -> Optional[Path]:
    config = load_config(args.config)
    ipc_dir = Path(args.ipc_dir).expanduser().resolve()
    ipc_dir.mkdir(parents=True, exist_ok=True)
    frame_path = ipc_dir / "latest_frame.rgb"
    result_path = ipc_dir / "latest_result.txt"
    status_path = ipc_dir / "worker_status.json"
    control_path = ipc_dir / "control.txt"
    latest_status: Dict[str, Any] = {}

    def write_status(extra: Optional[Dict[str, Any]] = None) -> None:
        value = {
            **runtime.state_machine.snapshot(),
            "status": runtime.status,
            "session_id": runtime.session_id,
            "pid": os.getpid(),
            "heartbeat_monotonic_ns": time.monotonic_ns(),
            "model_load_count": runtime.model_load_count,
            "warmup_count": runtime.warmup_count,
            "backend_initialization_count": runtime.backend_initialization_count,
            **latest_status,
            **(extra or {}),
        }
        _atomic_text(status_path, json.dumps(json_safe(value), separators=(",", ":")))

    def state_changed(_snapshot: Dict[str, Any]) -> None:
        write_status()

    runtime = InferenceRuntime(
        config, backend=args.backend, state_callback=state_changed
    )
    should_stop = False
    last_frame_index = -1
    last_snapshot_seq = 0
    last_running_control = False
    last_frame_arrival = time.monotonic()
    last_heartbeat = 0.0

    def _stop(_signum: int, _frame: object) -> None:
        nonlocal should_stop
        should_stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    write_status()
    poll_seconds = max(0.001, int(config["runtime"]["worker_poll_ms"]) / 1000)
    stream_timeout = float(config.get("live", {}).get("stream_timeout_ms", 5000)) / 1000
    target_fps = float(config.get("live", {}).get("target_inference_fps", 1.5))
    min_inference_interval = 1.0 / target_fps
    last_inference_started = float("-inf")
    try:
        while not should_stop:
            control = _read_control(control_path)
            running_control = bool(control["running"])
            if running_control and not last_running_control:
                runtime.plant_enabled = bool(control["plant"]) and bool(
                    config["models"]["detector"]["enabled"]
                )
                runtime.segmenter_enabled = bool(control["segmenter"]) and bool(
                    config["models"]["segmenter"]["enabled"]
                )
                try:
                    runtime.start()
                    last_frame_arrival = time.monotonic()
                except Exception as exc:
                    latest_status["warning"] = f"{type(exc).__name__}: {exc}"
            elif not running_control and last_running_control:
                runtime.stop()
                result_path.unlink(missing_ok=True)
                latest_status.clear()
            last_running_control = running_control

            now = time.monotonic()
            if now - last_heartbeat >= 0.5:
                write_status()
                last_heartbeat = now
            if runtime.state_machine.state != AppState.RUNNING:
                time.sleep(poll_seconds)
                continue
            if not frame_path.is_file():
                if now - last_frame_arrival > stream_timeout:
                    runtime.fail(
                        "STREAM_TIMEOUT",
                        f"Tidak ada frame liveview selama {stream_timeout:.1f} detik",
                        session_status="stream_error",
                    )
                    write_status()
                time.sleep(poll_seconds)
                continue
            try:
                envelope = read_frame(frame_path)
            except (OSError, ValueError) as exc:
                latest_status["frame_error"] = str(exc)
                write_status()
                time.sleep(poll_seconds)
                continue
            metadata = envelope.metadata
            if metadata.sequence == last_frame_index:
                if now - last_frame_arrival > stream_timeout:
                    runtime.fail(
                        "STREAM_TIMEOUT",
                        f"Frame sequence berhenti selama {stream_timeout:.1f} detik",
                        session_status="stream_error",
                    )
                    write_status()
                time.sleep(poll_seconds)
                continue
            last_frame_arrival = now
            if now - last_inference_started < min_inference_interval:
                time.sleep(poll_seconds)
                continue
            snapshot_seq = int(control["snapshot_seq"])
            snapshot = snapshot_seq != last_snapshot_seq
            last_snapshot_seq = snapshot_seq
            last_inference_started = time.monotonic()
            try:
                result, _overlay = runtime.infer_frame(
                    envelope.frame_bgr,
                    frame_index=metadata.sequence,
                    capture_timestamp=metadata.capture_timestamp,
                    camera_source=metadata.camera_source,
                    telemetry=envelope.telemetry,
                    snapshot=snapshot,
                    source_frame_id=metadata.source_frame_id,
                    capture_monotonic_ns=metadata.capture_monotonic_ns,
                    row_stride=metadata.row_stride,
                    pixel_format=metadata.pixel_format,
                )
            except Exception as exc:
                runtime.fail(
                    "INFERENCE_FAILED",
                    f"{type(exc).__name__}: {exc}",
                    session_status="inference_error",
                )
                write_status()
                continue
            _atomic_text(
                result_path,
                serialize_result(result, metadata.capture_monotonic_ns, runtime.status),
            )
            latest_status = {
                "frame_sequence": metadata.sequence,
                "source_frame_id": metadata.source_frame_id,
                "camera_source": metadata.camera_source,
                "resolution": [metadata.width, metadata.height],
                "row_stride": metadata.row_stride,
                "pixel_format": metadata.pixel_format,
                "ai_fps": result.metrics.get("ai_fps"),
                "latency_ms": result.inference_latency_ms,
                "p50_latency_ms": result.metrics.get("p50_latency_ms"),
                "plant_detections": len(result.plant_detections),
                "gap_candidates": None,
                "overlay_sent_objects": result.overlay.get("sent_objects"),
                "overlay_truncated_objects": result.overlay.get("truncated_objects"),
                "rtk_status": envelope.telemetry.rtk_status,
                "gps_signal_level": envelope.telemetry.gps_signal_level,
                "visible_satellites": envelope.telemetry.visible_satellites,
                "warnings": result.warning,
            }
            write_status()
            last_frame_index = metadata.sequence
        return runtime.shutdown("stopped")
    except Exception:
        if runtime.writer is not None:
            runtime.writer.logger.exception("worker_failed")
        runtime.fail("WORKER_FAILED", "Unhandled worker exception")
        runtime.shutdown("error")
        raise


def main() -> None:
    summary = run(build_parser().parse_args())
    if summary is not None:
        print(summary)


if __name__ == "__main__":
    main()
