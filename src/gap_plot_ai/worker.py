from __future__ import annotations

import argparse
import json
import os
import signal
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

import cv2
import numpy as np

from .config import load_config
from .runtime import InferenceRuntime
from .schema import Telemetry, json_safe

MAGIC = b"GPAIFRM1"
FRAME_HEADER = struct.Struct("<8sQQIIII6diI")
TELEMETRY_LAT = 1 << 0
TELEMETRY_LON = 1 << 1
TELEMETRY_REL_ALT = 1 << 2
TELEMETRY_ABS_ALT = 1 << 3
TELEMETRY_GIMBAL_PITCH = 1 << 4
TELEMETRY_HEADING = 1 << 5
TELEMETRY_RTK = 1 << 6


def _atomic_text(path: Path, value: str) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(value, encoding="utf-8")
    os.replace(temp, path)


def _read_control(path: Path) -> Dict[str, int]:
    control = {"running": 1, "plant": 1, "segmenter": 1, "snapshot_seq": 0}
    if not path.is_file():
        return control
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() in control:
            try:
                control[key.strip()] = int(value.strip())
            except ValueError:
                continue
    return control


def read_frame(path: Path) -> Tuple[int, int, np.ndarray, Telemetry]:
    payload = path.read_bytes()
    if len(payload) < FRAME_HEADER.size:
        raise ValueError("frame spool lebih pendek dari header")
    unpacked = FRAME_HEADER.unpack_from(payload)
    (
        magic,
        frame_index,
        capture_ns,
        width,
        height,
        channels,
        data_len,
        latitude,
        longitude,
        relative_altitude,
        absolute_altitude,
        gimbal_pitch,
        heading,
        rtk_status,
        valid_mask,
    ) = unpacked
    if magic != MAGIC or channels != 3:
        raise ValueError("magic/pixel channels spool tidak valid")
    expected = int(width) * int(height) * int(channels)
    if data_len != expected or len(payload) != FRAME_HEADER.size + expected:
        raise ValueError("ukuran RGB spool tidak konsisten")
    rgb = np.frombuffer(payload, dtype=np.uint8, offset=FRAME_HEADER.size)
    rgb = rgb.reshape((height, width, channels))
    frame_bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    telemetry = Telemetry(
        aircraft_latitude=latitude if valid_mask & TELEMETRY_LAT else None,
        aircraft_longitude=longitude if valid_mask & TELEMETRY_LON else None,
        relative_altitude=relative_altitude if valid_mask & TELEMETRY_REL_ALT else None,
        absolute_altitude=absolute_altitude if valid_mask & TELEMETRY_ABS_ALT else None,
        gimbal_pitch=gimbal_pitch if valid_mask & TELEMETRY_GIMBAL_PITCH else None,
        aircraft_heading=heading if valid_mask & TELEMETRY_HEADING else None,
        rtk_status=rtk_status if valid_mask & TELEMETRY_RTK else None,
        source_timestamp=datetime.fromtimestamp(
            capture_ns / 1_000_000_000, timezone.utc
        ).isoformat(),
    )
    return int(frame_index), int(capture_ns), frame_bgr, telemetry


def serialize_result(result: Any, capture_ns: int, status: str) -> str:
    fps = float(result.metrics.get("fps_session_average", 0))
    lines = [
        (
            f"RESULT {result.frame_index} {capture_ns} {time.time_ns()} "
            f"{result.inference_latency_ms:.6f} {fps:.6f} {status} {len(result.warning)}"
        )
    ]
    width = max(result.image_width, 1)
    height = max(result.image_height, 1)
    for detection_id, detection in enumerate(result.plant_detections):
        box = detection.get("bbox_xyxy", [])
        if len(box) != 4:
            continue
        lines.append(
            "BOX "
            f"{detection_id} {int(detection.get('class_id', 0))} "
            f"{float(detection.get('confidence', 0)):.8f} "
            f"{float(box[0]) / width:.8f} {float(box[1]) / height:.8f} "
            f"{float(box[2]) / width:.8f} {float(box[3]) / height:.8f}"
        )
    segmentation = result.plot_segmentation or {}
    for contour_id, contour in enumerate(segmentation.get("contours", [])):
        values = []
        for x, y in contour:
            values.extend((f"{float(x) / width:.8f}", f"{float(y) / height:.8f}"))
        lines.append(f"CONTOUR {contour_id} {len(contour)} " + " ".join(values))
    lines.append("END")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gap Plot AI Manifold spool worker")
    parser.add_argument("--config", required=True)
    parser.add_argument("--ipc-dir", required=True)
    parser.add_argument("--backend", choices=("auto", "pt", "onnx", "engine"), default="auto")
    return parser


def run(args: argparse.Namespace) -> Path:
    config = load_config(args.config)
    ipc_dir = Path(args.ipc_dir).expanduser().resolve()
    ipc_dir.mkdir(parents=True, exist_ok=True)
    frame_path = ipc_dir / "latest_frame.rgb"
    result_path = ipc_dir / "latest_result.txt"
    status_path = ipc_dir / "worker_status.json"
    control_path = ipc_dir / "control.txt"
    runtime = InferenceRuntime(config, backend=args.backend)
    should_stop = False
    last_frame_index = -1
    last_snapshot_seq = 0

    def _stop(_signum: int, _frame: object) -> None:
        nonlocal should_stop
        should_stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    _atomic_text(
        status_path,
        json.dumps(
            {"status": runtime.status, "session_id": runtime.session_id, "pid": os.getpid()}
        ),
    )
    poll_seconds = max(0.001, int(config["runtime"]["worker_poll_ms"]) / 1000)
    final_status = "stopped"
    try:
        while not should_stop:
            if not frame_path.is_file():
                time.sleep(poll_seconds)
                continue
            try:
                frame_index, capture_ns, frame_bgr, telemetry = read_frame(frame_path)
            except (OSError, ValueError) as exc:
                _atomic_text(
                    status_path,
                    json.dumps({"status": "Error", "warning": str(exc), "pid": os.getpid()}),
                )
                time.sleep(poll_seconds)
                continue
            if frame_index == last_frame_index:
                time.sleep(poll_seconds)
                continue
            control = _read_control(control_path)
            runtime.set_controls(
                running=bool(control["running"]),
                plant_enabled=bool(control["plant"]),
                segmenter_enabled=bool(control["segmenter"]),
            )
            snapshot_seq = control["snapshot_seq"]
            snapshot = snapshot_seq != last_snapshot_seq
            last_snapshot_seq = snapshot_seq
            capture_timestamp = datetime.fromtimestamp(
                capture_ns / 1_000_000_000, timezone.utc
            ).isoformat()
            result, _overlay = runtime.process_frame(
                frame_bgr,
                frame_index=frame_index,
                capture_timestamp=capture_timestamp,
                camera_source="DJI_LIVEVIEW_CAMERA_SOURCE_M4E_VIS",
                telemetry=telemetry,
                snapshot=snapshot,
            )
            _atomic_text(
                result_path, serialize_result(result, capture_ns, runtime.status)
            )
            _atomic_text(
                status_path,
                json.dumps(
                    json_safe(
                        {
                            "status": runtime.status,
                            "session_id": runtime.session_id,
                            "frame_index": frame_index,
                            "fps": result.metrics.get("fps_session_average"),
                            "latency_ms": result.inference_latency_ms,
                            "warnings": result.warning,
                            "pid": os.getpid(),
                        }
                    )
                ),
            )
            last_frame_index = frame_index
        return runtime.close(final_status)
    except Exception:
        final_status = "error"
        runtime.writer.logger.exception("worker_failed")
        runtime.close(final_status)
        raise


def main() -> None:
    summary = run(build_parser().parse_args())
    print(summary)


if __name__ == "__main__":
    main()
