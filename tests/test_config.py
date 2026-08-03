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


def test_live_config_extends_baseline_without_second_threshold_source() -> None:
    root = Path(__file__).parents[1]
    base = load_config(root / "config/app.yaml")
    live = load_config(root / "config/live.yaml")
    assert live["models"]["detector"]["tile_size"] == base["models"]["detector"]["tile_size"]
    assert live["models"]["detector"]["confidence_threshold"] == base["models"]["detector"]["confidence_threshold"]
    assert live["models"]["detector"]["enable_center_suppression"] is False
    assert live["live"]["frame_queue_size"] == 1
    assert live["models"]["segmenter"]["enabled"] is False


def test_config_uses_explicit_application_root_for_packaged_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    packaged_root = tmp_path / "package"
    monkeypatch.setenv("GAP_PLOT_AI_APP_ROOT", str(packaged_root))
    root = Path(__file__).parents[1]
    config = load_config(root / "config/live.yaml")
    assert config["_repo_root"] == str(packaged_root.resolve())
    assert config["runtime"]["root"] == str((packaged_root / "runtime").resolve())
    assert config["models"]["detector"]["engine_path"].startswith(
        str((packaged_root / "models").resolve())
    )


def test_config_uses_writable_runtime_override(tmp_path: Path, monkeypatch) -> None:
    runtime_root = tmp_path / "data" / "runtime"
    monkeypatch.setenv("GAP_PLOT_AI_RUNTIME_ROOT", str(runtime_root))
    root = Path(__file__).parents[1]
    config = load_config(root / "config/live.yaml")
    assert config["runtime"]["root"] == str(runtime_root.resolve())


def test_config_rejects_python_global_nms(config_dict: dict) -> None:
    config = deepcopy(config_dict)
    config["models"]["detector"]["global_nms_backend"] = "python"
    with pytest.raises(ConfigError, match="TorchVision|torchvision"):
        validate_config(config)


def test_config_rejects_reenabled_center_suppression(config_dict: dict) -> None:
    config = deepcopy(config_dict)
    config["models"]["detector"]["enable_center_suppression"] = True
    with pytest.raises(ConfigError, match="center_suppression"):
        validate_config(config)
