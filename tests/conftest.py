from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def config_dict(tmp_path: Path) -> dict:
    root = Path(__file__).parents[1]
    raw = yaml.safe_load((root / "config/app.example.yaml").read_text(encoding="utf-8"))
    config = deepcopy(raw)
    config["runtime"]["root"] = str(tmp_path / "runtime")
    config["runtime"]["device"] = "cpu"
    config["storage"]["max_session_bytes"] = 10 * 1024 * 1024
    config["storage"]["log_rotate_bytes"] = 4096
    config["models"]["detector"]["path"] = str(tmp_path / "detector.pt")
    config["models"]["segmenter"]["path"] = str(tmp_path / "segmenter.pt")
    return config
