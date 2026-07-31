from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from gap_plot_ai.config import ConfigError, load_config, validate_config


def test_config_validation_accepts_audited_defaults(config_dict: dict) -> None:
    validate_config(config_dict)


def test_config_rejects_non_wide_camera(config_dict: dict) -> None:
    config = deepcopy(config_dict)
    config["camera"]["source"] = "DJI_LIVEVIEW_CAMERA_SOURCE_M4T_IR"
    with pytest.raises(ConfigError, match="M4E_VIS"):
        validate_config(config)


def test_config_rejects_unbounded_or_zero_queue(config_dict: dict) -> None:
    config = deepcopy(config_dict)
    config["runtime"]["queue_capacity"] = 0
    with pytest.raises(ConfigError, match="queue_capacity"):
        validate_config(config)


def test_load_config_rejects_unexpanded_environment(tmp_path: Path, config_dict: dict) -> None:
    config_dict["runtime"]["root"] = "${GAP_PLOT_AI_UNSET_TEST}/runtime"
    path = tmp_path / "app.yaml"
    path.write_text(yaml.safe_dump(config_dict), encoding="utf-8")
    with pytest.raises(ConfigError, match="belum terisi"):
        load_config(path)


def test_config_rejects_invalid_detector_tile_overlap(config_dict: dict) -> None:
    config = deepcopy(config_dict)
    config["models"]["detector"]["tile_overlap"] = 1024
    with pytest.raises(ConfigError, match="tile_size/overlap"):
        validate_config(config)
