from __future__ import annotations

from pathlib import Path

import numpy as np

from gap_plot_ai.schema import ModelOutput


class FakeModel:
    def __init__(self, config: dict, *, backend: str, device: str):
        self.config = config
        self.path = Path(f"{config['task']}.pt")
        self.sha256 = config["source_sha256"]

    def audit(self) -> dict:
        return {"path": str(self.path), "sha256": self.sha256}

    def predict(self, image: np.ndarray) -> ModelOutput:
        if self.config["task"] == "detect":
            return ModelOutput(
                detections=[
                    {
                        "bbox_xyxy": [1.0, 2.0, 10.0, 12.0],
                        "confidence": 0.9,
                        "class_id": 0,
                        "class_name": "plant",
                    }
                ],
                latency_ms=1.0,
            )
        return ModelOutput(
            contours=[[[0.0, 0.0], [20.0, 0.0], [20.0, 20.0]]],
            latency_ms=2.0,
        )


def test_runtime_shutdown_writes_all_session_outputs(
    tmp_path: Path, config_dict: dict, monkeypatch
) -> None:
    import gap_plot_ai.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "UltralyticsModel", FakeModel)
    runtime = runtime_module.InferenceRuntime(config_dict, backend="pt")
    frame = np.zeros((24, 32, 3), dtype=np.uint8)
    result, overlay = runtime.process_frame(
        frame,
        frame_index=0,
        capture_timestamp="unknown",
        camera_source="offline_wide_video",
        snapshot=True,
    )
    assert len(result.plant_detections) == 1
    assert result.plot_segmentation is not None
    assert overlay.shape == frame.shape
    summary = runtime.close("completed")
    assert summary.is_file()
    assert (summary.parent / "detections.jsonl").is_file()
    assert runtime.close("completed") == summary


def test_runtime_stop_is_idempotent_and_restart_reuses_models(
    config_dict: dict, monkeypatch
) -> None:
    import gap_plot_ai.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "UltralyticsModel", FakeModel)
    runtime = runtime_module.InferenceRuntime(config_dict, backend="pt")
    result, _ = runtime.process_frame(
        np.zeros((24, 32, 3), dtype=np.uint8),
        frame_index=1,
        capture_timestamp="unknown",
        camera_source="offline_wide_video",
    )
    assert result.plant_detections
    load_count = runtime.model_load_count
    first_summary = runtime.stop()
    assert runtime.status == "IDLE"
    assert runtime.stop() == first_summary
    assert runtime.start()
    assert runtime.model_load_count == load_count
    assert runtime.warmup_count == 1
    runtime.close()


def test_runtime_marks_reused_segmentation_without_double_counting_latency(
    config_dict: dict, monkeypatch
) -> None:
    import gap_plot_ai.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "UltralyticsModel", FakeModel)
    runtime = runtime_module.InferenceRuntime(config_dict, backend="pt")
    frame = np.zeros((24, 32, 3), dtype=np.uint8)
    runtime.process_frame(
        frame,
        frame_index=0,
        capture_timestamp="unknown",
        camera_source="offline_wide_video",
    )
    result, _ = runtime.process_frame(
        frame,
        frame_index=1,
        capture_timestamp="unknown",
        camera_source="offline_wide_video",
    )
    runtime.close()
    assert result.metrics["segmenter_ran"] is False
    assert result.metrics["segmenter_reused"] is True
    assert result.metrics["segmenter_latency_ms"] == 0.0
    assert result.metrics["segmenter_cached_output_latency_ms"] == 2.0
