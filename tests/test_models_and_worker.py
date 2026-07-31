from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from gap_plot_ai.models import UltralyticsModel
from gap_plot_ai.schema import FrameResult
from gap_plot_ai.worker import FRAME_HEADER, MAGIC, read_frame, serialize_result


class EmptyPredictModel(UltralyticsModel):
    def __init__(self) -> None:
        self.expected_task = "detect"
        self.config = {}

    def _predict(self, image_bgr: np.ndarray):
        return []


class FailingPredictModel(EmptyPredictModel):
    def _predict(self, image_bgr: np.ndarray):
        raise RuntimeError("malformed")


def test_empty_and_malformed_model_output_do_not_crash() -> None:
    frame = np.zeros((12, 16, 3), dtype=np.uint8)
    empty = EmptyPredictModel().predict(frame)
    failed = FailingPredictModel().predict(frame)
    assert empty.warning == "empty_model_output"
    assert failed.warning == "RuntimeError: malformed"


def test_worker_rejects_truncated_frame(tmp_path: Path) -> None:
    path = tmp_path / "latest_frame.rgb"
    path.write_bytes(b"short")
    try:
        read_frame(path)
    except ValueError as exc:
        assert "header" in str(exc)
    else:
        raise AssertionError("truncated frame harus ditolak")


def test_worker_decodes_rgb_frame_and_null_telemetry(tmp_path: Path) -> None:
    width, height = 4, 3
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[..., 0] = 255
    data = rgb.tobytes()
    header = FRAME_HEADER.pack(
        MAGIC,
        9,
        1_700_000_000_000_000_000,
        width,
        height,
        3,
        len(data),
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0,
        0,
    )
    path = tmp_path / "latest_frame.rgb"
    path.write_bytes(header + data)
    index, capture_ns, bgr, telemetry = read_frame(path)
    assert index == 9
    assert capture_ns == 1_700_000_000_000_000_000
    assert bgr.shape == (height, width, 3)
    assert bgr[0, 0].tolist() == [0, 0, 255]
    assert telemetry.aircraft_latitude is None


def test_synthetic_known_box_maps_to_normalized_pilot_coordinates() -> None:
    result = FrameResult(
        session_id="test",
        frame_index=7,
        capture_timestamp="unknown",
        inference_timestamp="unknown",
        camera_source="M4E_VIS",
        image_width=1000,
        image_height=500,
        plant_detections=[
            {
                "bbox_xyxy": [100.0, 50.0, 900.0, 450.0],
                "confidence": 0.75,
                "class_id": 0,
            }
        ],
        plot_segmentation=None,
        model_version={},
        model_sha256={},
        confidence_threshold={},
        inference_latency_ms=5.0,
        aircraft_latitude=None,
        aircraft_longitude=None,
        relative_altitude=None,
        absolute_altitude=None,
        gimbal_pitch=None,
        aircraft_heading=None,
        rtk_status=None,
        warning=[],
        metrics={"fps_session_average": 2.0},
    )
    payload = serialize_result(result, 123, "Running")
    box_line = next(line for line in payload.splitlines() if line.startswith("BOX "))
    assert box_line == "BOX 0 0 0.75000000 0.10000000 0.10000000 0.90000000 0.90000000"
