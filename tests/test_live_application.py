from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterator

import numpy as np
import pytest

from gap_plot_ai.fake_backend import FakeModel
from gap_plot_ai.live import LiveApplication
from gap_plot_ai.runtime import InferenceRuntime
from gap_plot_ai.sources import ArrayFrameSource, FrameEnvelope
from gap_plot_ai.state import AppState


def _frames(count: int, width: int = 160, height: int = 96):
    return [np.zeros((height, width, 3), dtype=np.uint8) for _ in range(count)]


def test_replay_latest_frame_drop_single_worker_and_restart(live_config_dict: dict) -> None:
    frames = _frames(40)
    application = LiveApplication(
        live_config_dict,
        lambda: ArrayFrameSource(frames, source_fps=30),
        backend="pt",
        model_factory=FakeModel,
    )
    assert application.start()
    assert not application.start()
    assert application.wait(5)
    assert application._queue is not None
    assert application._queue.stats().dropped > 0
    assert application.runtime.model_load_count == 1
    assert application.runtime.backend_initialization_count == 1
    assert application.runtime.warmup_count == 1
    assert application.worker_start_count == 1
    assert application.start()
    assert application.wait(5)
    assert application.runtime.model_load_count == 1
    assert application.runtime.backend_initialization_count == 1
    assert application.runtime.warmup_count == 1
    assert application.worker_start_count == 2
    application.shutdown()


def test_shutdown_waits_for_busy_inference(live_config_dict: dict) -> None:
    config = live_config_dict
    config["models"]["detector"]["fake_delay_seconds"] = 0.2
    application = LiveApplication(
        config,
        lambda: ArrayFrameSource(_frames(3)),
        backend="pt",
        model_factory=FakeModel,
    )
    application.start()
    time.sleep(0.05)
    assert application.stop(timeout=2.0)
    assert application.runtime.state_machine.state in {AppState.IDLE, AppState.ERROR}
    application.shutdown()


class BrokenSource:
    def frames(self) -> Iterator[FrameEnvelope]:
        raise OSError("camera disconnected")
        yield

    def close(self) -> None:
        return None


def test_stream_disconnect_transitions_error_and_closes_session(
    live_config_dict: dict,
) -> None:
    application = LiveApplication(
        live_config_dict,
        BrokenSource,
        backend="pt",
        model_factory=FakeModel,
    )
    application.start()
    assert application.wait(5)
    assert application.runtime.state_machine.state == AppState.ERROR
    summary_path = application.runtime._last_summary
    assert summary_path is not None
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["status"] == "stream_error"
    application.shutdown()


def test_missing_engine_is_recoverable_visible_error(live_config_dict: dict) -> None:
    runtime = InferenceRuntime(live_config_dict, backend="engine")
    with pytest.raises(FileNotFoundError, match="engine"):
        runtime.start()
    assert runtime.state_machine.state == AppState.ERROR
    assert runtime.state_machine.last_error is not None
    assert runtime.state_machine.last_error.recoverable is True


def test_replay_session_contains_all_required_jsonl(live_config_dict: dict) -> None:
    application = LiveApplication(
        live_config_dict,
        lambda: ArrayFrameSource(_frames(8)),
        backend="pt",
        model_factory=FakeModel,
    )
    application.start()
    assert application.wait(5)
    summary = application.runtime._last_summary
    assert summary is not None and summary.is_file()
    for filename in (
        "detections.jsonl",
        "telemetry.jsonl",
        "metrics.jsonl",
        "errors.log",
        "session_summary.json",
    ):
        assert (summary.parent / filename).exists()
    record = json.loads(
        (summary.parent / "detections.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert record["schema_version"] == "1.0"
    assert record["gap_candidates"] is None
    assert record["overlay"]["total_detections"] >= record["overlay"]["sent_objects"]
    application.shutdown()
