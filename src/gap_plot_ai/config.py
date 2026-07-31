from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when the runtime configuration is unsafe or incomplete."""


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        expanded = os.path.expandvars(os.path.expanduser(value))
        if "${" in expanded:
            raise ConfigError(f"Environment variable belum terisi pada path: {value}")
        return expanded
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item) for item in value]
    return value


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(f"Config tidak ditemukan: {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError("Config root harus berupa mapping YAML.")
    config = _expand(deepcopy(raw))
    validate_config(config)
    config["_config_path"] = str(config_path)
    return config


def validate_config(config: dict[str, Any], *, require_model_files: bool = False) -> None:
    for section in ("app", "camera", "runtime", "models", "overlay", "storage"):
        if not isinstance(config.get(section), dict):
            raise ConfigError(f"Section '{section}' wajib ada dan berupa mapping.")

    runtime = config["runtime"]
    capacity = int(runtime.get("queue_capacity", 0))
    if not 1 <= capacity <= 8:
        raise ConfigError("runtime.queue_capacity harus 1..8.")

    camera = config["camera"]
    if camera.get("source") != "DJI_LIVEVIEW_CAMERA_SOURCE_M4E_VIS":
        raise ConfigError("MVP hanya mengizinkan source wide/visual Matrice 4E (M4E_VIS).")
    if camera.get("pixel_format") != "PIXFMT_RGB_PACKED":
        raise ConfigError("Frontend/worker MVP mengharuskan PIXFMT_RGB_PACKED.")

    for model_name, expected_task in (("detector", "detect"), ("segmenter", "segment")):
        model = config["models"].get(model_name)
        if not isinstance(model, dict):
            raise ConfigError(f"models.{model_name} wajib ada.")
        if model.get("task") != expected_task:
            raise ConfigError(f"models.{model_name}.task harus '{expected_task}'.")
        if int(model.get("image_size", 0)) <= 0:
            raise ConfigError(f"models.{model_name}.image_size harus positif.")
        confidence = float(model.get("confidence_threshold", -1))
        iou = float(model.get("nms_iou_threshold", -1))
        if not 0 <= confidence <= 1 or not 0 <= iou <= 1:
            raise ConfigError(f"Threshold models.{model_name} harus dalam rentang 0..1.")
        if int(model.get("interval_frames", 0)) < 1:
            raise ConfigError(f"models.{model_name}.interval_frames minimal 1.")
        if model_name == "detector" and bool(model.get("tiled", False)):
            tile_size = int(model.get("tile_size", 0))
            overlap = int(model.get("tile_overlap", -1))
            if tile_size <= 0 or not 0 <= overlap < tile_size:
                raise ConfigError(
                    "models.detector tile_size/overlap tidak valid."
                )
            global_iou = float(model.get("global_nms_iou", -1))
            if not 0 <= global_iou <= 1:
                raise ConfigError(
                    "models.detector.global_nms_iou harus dalam rentang 0..1."
                )
            if int(model.get("max_detections_full_frame", 0)) < 1:
                raise ConfigError(
                    "models.detector.max_detections_full_frame minimal 1."
                )
        if require_model_files and bool(model.get("enabled", True)):
            model_path = Path(str(model.get("path", "")))
            if not model_path.is_file():
                raise ConfigError(f"Weight {model_name} tidak ditemukan: {model_path}")

    storage = config["storage"]
    if int(storage.get("max_session_bytes", 0)) < 1024 * 1024:
        raise ConfigError("storage.max_session_bytes minimal 1 MiB.")
    if int(storage.get("log_rotate_bytes", 0)) < 1024:
        raise ConfigError("storage.log_rotate_bytes minimal 1 KiB.")
    if int(storage.get("jsonl_rotate_bytes", 0)) < 1024:
        raise ConfigError("storage.jsonl_rotate_bytes minimal 1 KiB.")
    if not 1 <= int(storage.get("jsonl_backup_count", 0)) <= 20:
        raise ConfigError("storage.jsonl_backup_count harus 1..20.")
