from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def test_offline_cli_path_still_processes_video(
    tmp_path: Path, config_dict: dict, monkeypatch
) -> None:
    import gap_plot_ai.runtime as runtime_module
    from gap_plot_ai.fake_backend import FakeModel
    from gap_plot_ai.offline import run

    monkeypatch.setattr(runtime_module, "UltralyticsModel", FakeModel)
    config_dict["runtime"]["root"] = str(tmp_path / "runtime")
    config_path = tmp_path / "app.yaml"
    import yaml

    config_path.write_text(yaml.safe_dump(config_dict), encoding="utf-8")
    video_path = tmp_path / "fixture.mp4"
    writer = cv2.VideoWriter(
        str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), 5.0, (64, 48)
    )
    assert writer.isOpened()
    for _ in range(3):
        writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
    writer.release()
    args = argparse.Namespace(
        config=str(config_path),
        video=str(video_path),
        backend="pt",
        start_frame=0,
        max_frames=2,
        stride=1,
        snapshot_every=0,
        preview=False,
    )
    summary = run(args)
    payload = json.loads(summary.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["inference_records"] == 2
