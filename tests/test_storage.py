from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from gap_plot_ai.schema import FrameResult, Telemetry
from gap_plot_ai.storage import SessionWriter


def _result(session_id: str) -> FrameResult:
    return FrameResult(
        session_id=session_id,
        frame_index=1,
        capture_timestamp="unknown",
        inference_timestamp="2026-07-31T00:00:00+00:00",
        camera_source="offline_wide_video",
        image_width=32,
        image_height=24,
        plant_detections=[],
        plot_segmentation={"representation": "contours", "contours": []},
        model_version={"detector": "a", "segmenter": "b"},
        model_sha256={"detector": "1", "segmenter": "2"},
        confidence_threshold={"detector": 0.2, "segmenter": 0.03},
        inference_latency_ms=float("nan"),
        aircraft_latitude=None,
        aircraft_longitude=None,
        relative_altitude=None,
        absolute_altitude=None,
        gimbal_pitch=None,
        aircraft_heading=None,
        rtk_status=None,
        warning=[],
        metrics={"fps": float("inf")},
    )


def test_jsonl_serialization_is_valid_and_nonfinite_becomes_null(
    tmp_path: Path, config_dict: dict
) -> None:
    writer = SessionWriter(
        tmp_path,
        config_dict["storage"],
        {"application": {"name": "test"}},
    )
    writer.append(_result(writer.session_id), Telemetry())
    writer.close("completed")
    record = json.loads(
        (writer.session_dir / "detections.jsonl").read_text(encoding="utf-8")
    )
    assert record["inference_latency_ms"] is None
    assert record["metrics"]["fps"] is None


def test_snapshot_and_idempotent_graceful_close(tmp_path: Path, config_dict: dict) -> None:
    writer = SessionWriter(tmp_path, config_dict["storage"], {})
    frame = np.zeros((24, 32, 3), dtype=np.uint8)
    assert writer.save_snapshot(frame, 7) is not None
    first = writer.close("stopped")
    second = writer.close("stopped")
    assert first == second
    summary = json.loads(first.read_text(encoding="utf-8"))
    assert summary["snapshots"] == 1
    assert writer.session_id.endswith("Z")
    assert "T" in writer.session_id
    assert (writer.session_dir / "frames").is_dir()
    assert (writer.session_dir / "overlays").is_dir()
    assert (writer.session_dir / "errors.log").is_file()


def test_jsonl_rotation_bounds_files_and_reports_discard(
    tmp_path: Path, config_dict: dict
) -> None:
    storage = dict(config_dict["storage"])
    storage["max_session_bytes"] = 1024 * 1024
    storage["jsonl_rotate_bytes"] = 1024
    storage["jsonl_backup_count"] = 1
    storage["log_rotate_bytes"] = 1024
    storage["log_backup_count"] = 1
    writer = SessionWriter(tmp_path, storage, {})
    for frame_index in range(40):
        result = _result(writer.session_id)
        result.frame_index = frame_index
        result.warning = ["x" * 200]
        writer.append(result, Telemetry())
    summary_path = writer.close("completed")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["jsonl_rotations"] > 0
    assert summary["jsonl_files_discarded"] > 0
    assert len(list(summary_path.parent.glob("detections*.jsonl"))) <= 2
    assert summary["storage_cap_reached"] is True
