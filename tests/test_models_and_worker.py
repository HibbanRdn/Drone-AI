from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from gap_plot_ai.models import UltralyticsModel
from gap_plot_ai.schema import FrameResult
from gap_plot_ai.worker import (
    FRAME_HEADER,
    FRAME_HEADER_VERSION,
    MAGIC,
    TELEMETRY_ATTITUDE,
    TELEMETRY_GIMBAL,
    TELEMETRY_GPS_QUALITY,
    TELEMETRY_LAT,
    TELEMETRY_LON,
    TELEMETRY_RTK,
    TELEMETRY_VELOCITY,
    read_frame,
    serialize_result,
)


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


def test_export_backend_is_initialized_once_and_names_are_deferred(
    tmp_path: Path, monkeypatch
) -> None:
    import ultralytics

    class EmptyBoxes:
        def __len__(self) -> int:
            return 0

    class FakeExportYOLO:
        temporary_names_reads = 0

        def __init__(self, path: str, task: str) -> None:
            self.model = path
            self.task = task
            self.predictor = None

        @property
        def names(self):
            type(self).temporary_names_reads += 1
            return {0: "plant"}

        def predict(self, **_kwargs):
            if self.predictor is None:
                backend = SimpleNamespace(names={0: "plant"})
                self.predictor = SimpleNamespace(model=backend)
            return [
                SimpleNamespace(
                    names={0: "plant"}, speed={}, boxes=EmptyBoxes()
                )
            ]

    monkeypatch.setattr(ultralytics, "YOLO", FakeExportYOLO)
    engine = tmp_path / "detector.engine"
    engine.write_bytes(b"fake-engine")
    model = UltralyticsModel(
        {
            "task": "detect",
            "engine_path": str(engine),
            "image_size": 32,
            "confidence_threshold": 0.2,
            "nms_iou_threshold": 0.1,
            "max_detections": 100,
            "class_names": {0: "plant"},
        },
        backend="engine",
        device=0,
    )
    assert FakeExportYOLO.temporary_names_reads == 0
    model.predict(np.zeros((32, 32, 3), dtype=np.uint8))
    model.predict(np.zeros((32, 32, 3), dtype=np.uint8))
    assert model.audit()["backend_initialization_count"] == 1
    assert model.audit()["names_validated"] is True


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
        FRAME_HEADER_VERSION,
        5,
        9,
        42,
        1_000,
        1_700_000_000_000_000_000,
        1_001,
        width,
        height,
        width * 3,
        3,
        len(data),
        *([0.0] * 14),
        0,
        -1,
        0,
        0,
    )
    path = tmp_path / "latest_frame.rgb"
    path.write_bytes(header + data)
    envelope = read_frame(path)
    assert envelope.metadata.sequence == 9
    assert envelope.metadata.source_frame_id == 42
    assert envelope.metadata.capture_wall_clock_ns == 1_700_000_000_000_000_000
    assert envelope.frame_bgr.shape == (height, width, 3)
    assert envelope.frame_bgr[0, 0].tolist() == [0, 0, 255]
    assert envelope.telemetry.aircraft_latitude is None


def test_worker_decodes_rgb_frame_with_row_padding(tmp_path: Path) -> None:
    width, height = 2, 2
    packed_stride = width * 3
    row_stride = packed_stride + 2
    rgb_rows = np.asarray(
        [
            [255, 0, 0, 0, 255, 0, 99, 99],
            [0, 0, 255, 255, 255, 255, 88, 88],
        ],
        dtype=np.uint8,
    )
    data = rgb_rows.tobytes()
    header = FRAME_HEADER.pack(
        MAGIC,
        FRAME_HEADER_VERSION,
        5,
        10,
        43,
        2_000,
        1_700_000_000_000_000_001,
        2_001,
        width,
        height,
        row_stride,
        3,
        len(data),
        *([0.0] * 14),
        -1,
        -1,
        0,
        0,
    )
    path = tmp_path / "latest_frame.rgb"
    path.write_bytes(header + data)
    envelope = read_frame(path)
    assert envelope.metadata.row_stride == row_stride
    assert envelope.frame_bgr.shape == (height, width, 3)
    np.testing.assert_array_equal(envelope.frame_bgr[0, 0], [0, 0, 255])


def test_worker_associates_telemetry_with_monotonic_frame_timestamp(
    tmp_path: Path,
) -> None:
    width, height = 2, 2
    data = bytes(width * height * 3)
    values = [
        -6.2,
        106.8,
        15.0,
        120.0,
        1.0,
        2.0,
        3.0,
        4.0,
        5.0,
        6.0,
        0.5,
        0.25,
        -0.1,
        0.57,
    ]
    mask = (
        TELEMETRY_LAT
        | TELEMETRY_LON
        | TELEMETRY_ATTITUDE
        | TELEMETRY_GIMBAL
        | TELEMETRY_VELOCITY
        | TELEMETRY_GPS_QUALITY
        | TELEMETRY_RTK
    )
    header = FRAME_HEADER.pack(
        MAGIC,
        FRAME_HEADER_VERSION,
        5,
        77,
        88,
        999_000,
        1_700_000_000_000_000_000,
        999_250,
        width,
        height,
        width * 3,
        3,
        len(data),
        *values,
        4,
        5,
        17,
        mask,
    )
    path = tmp_path / "latest_frame.rgb"
    path.write_bytes(header + data)
    envelope = read_frame(path)
    assert envelope.metadata.sequence == 77
    assert envelope.metadata.capture_monotonic_ns == 999_000
    assert envelope.telemetry.source_monotonic_ns == 999_250
    assert envelope.telemetry.aircraft_latitude == -6.2
    assert envelope.telemetry.aircraft_yaw == 3.0
    assert envelope.telemetry.gimbal_yaw == 6.0
    assert envelope.telemetry.speed_mps == 0.57
    assert envelope.telemetry.gps_signal_level == 5
    assert envelope.telemetry.visible_satellites == 17
    assert envelope.telemetry.rtk_status == 4


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
