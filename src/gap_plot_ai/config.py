from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Union

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


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if key == "extends":
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _load_yaml_with_extends(config_path: Path, chain: List[Path]) -> Dict[str, Any]:
    if config_path in chain:
        cycle = " -> ".join(str(item) for item in [*chain, config_path])
        raise ConfigError(f"Config extends membentuk siklus: {cycle}")
    if not config_path.is_file():
        raise ConfigError(f"Config tidak ditemukan: {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError("Config root harus berupa mapping YAML.")
    parent_value = raw.get("extends")
    if parent_value is None:
        return raw
    if not isinstance(parent_value, str) or not parent_value.strip():
        raise ConfigError("Config extends harus berupa path YAML non-empty.")
    parent = Path(parent_value).expanduser()
    if not parent.is_absolute():
        parent = config_path.parent / parent
    parent = parent.resolve()
    return _deep_merge(_load_yaml_with_extends(parent, [*chain, config_path]), raw)


def load_config(path: Union[str, Path]) -> Dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    raw = _load_yaml_with_extends(config_path, [])
    config = _expand(deepcopy(raw))
    application_root = os.environ.get("GAP_PLOT_AI_APP_ROOT")
    repo_root = (
        Path(application_root).expanduser().resolve()
        if application_root
        else config_path.parent.parent
    )
    _resolve_repository_paths(config, repo_root)
    validate_config(config)
    config["_config_path"] = str(config_path)
    config["_repo_root"] = str(repo_root)
    return config


def _resolve_repository_paths(config: Dict[str, Any], repo_root: Path) -> None:
    """Resolve checked-in relative paths from the repository root, not the CWD."""

    runtime = config.get("runtime", {})
    if "root" in runtime:
        runtime_override = os.environ.get("GAP_PLOT_AI_RUNTIME_ROOT")
        runtime["root"] = str(
            Path(runtime_override).expanduser().resolve()
            if runtime_override
            else _repo_path(runtime["root"], repo_root)
        )
    for model in config.get("models", {}).values():
        if not isinstance(model, dict):
            continue
        for key in ("path", "onnx_path", "engine_path"):
            if key in model:
                model[key] = str(_repo_path(model[key], repo_root))


def _repo_path(value: Any, repo_root: Path) -> Path:
    path = Path(str(value)).expanduser()
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def validate_config(
    config: Dict[str, Any], *, require_model_files: bool = False
) -> None:
    for section in (
        "app",
        "camera",
        "runtime",
        "models",
        "overlay",
        "storage",
        "logging",
    ):
        if not isinstance(config.get(section), dict):
            raise ConfigError(f"Section '{section}' wajib ada dan berupa mapping.")

    runtime = config["runtime"]
    capacity = int(runtime.get("queue_capacity", 0))
    if capacity != 1:
        raise ConfigError("runtime.queue_capacity harus 1 untuk latest-frame semantics.")

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
            if model.get("global_nms_backend") != "torchvision":
                raise ConfigError(
                    "models.detector.global_nms_backend harus torchvision; "
                    "Python NMS lama tidak diizinkan."
                )
            if int(model.get("max_detections_full_frame", 0)) < 1:
                raise ConfigError(
                    "models.detector.max_detections_full_frame minimal 1."
                )
            if bool(model.get("enable_center_suppression", False)):
                raise ConfigError(
                    "models.detector.enable_center_suppression harus false "
                    "untuk baseline B0 manual v1 tervalidasi."
                )
        if require_model_files and bool(model.get("enabled", True)):
            model_path = Path(str(model.get("path", "")))
            if not model_path.is_file():
                raise ConfigError(f"Weight {model_name} tidak ditemukan: {model_path}")

    storage = config["storage"]
    if int(storage.get("max_session_bytes", 0)) < 1024 * 1024:
        raise ConfigError("storage.max_session_bytes minimal 1 MiB.")
    if int(storage.get("min_free_bytes", 128 * 1024 * 1024)) < 128 * 1024 * 1024:
        raise ConfigError("storage.min_free_bytes minimal 128 MiB.")
    if int(storage.get("log_rotate_bytes", 0)) < 1024:
        raise ConfigError("storage.log_rotate_bytes minimal 1 KiB.")
    if int(storage.get("jsonl_rotate_bytes", 0)) < 1024:
        raise ConfigError("storage.jsonl_rotate_bytes minimal 1 KiB.")
    if not 1 <= int(storage.get("jsonl_backup_count", 0)) <= 20:
        raise ConfigError("storage.jsonl_backup_count harus 1..20.")

    overlay = config["overlay"]
    if not isinstance(overlay.get("enabled"), bool):
        raise ConfigError("overlay.enabled harus boolean.")
    max_objects = overlay.get("max_objects")
    if max_objects != "auto":
        try:
            max_objects_value = int(max_objects)
        except (TypeError, ValueError) as exc:
            raise ConfigError("overlay.max_objects harus auto atau integer 1..255.") from exc
        if not 1 <= max_objects_value <= 255:
            raise ConfigError("overlay.max_objects harus auto atau integer 1..255.")
    if int(overlay.get("stale_result_timeout_ms", 0)) < 100:
        raise ConfigError("overlay.stale_result_timeout_ms minimal 100 ms.")
    if overlay.get("mode") != "compact":
        raise ConfigError("overlay.mode DEV saat ini harus compact.")
    if overlay.get("selection_strategy") not in {"confidence", "confidence_central"}:
        raise ConfigError("overlay.selection_strategy tidak didukung.")
    if overlay.get("aspect_mode") not in {"stretch", "contain", "cover"}:
        raise ConfigError("overlay.aspect_mode harus stretch/contain/cover.")
    if int(overlay.get("rotation_degrees", 0)) not in {0, 90, 180, 270}:
        raise ConfigError("overlay.rotation_degrees harus 0/90/180/270.")

    live = config.get("live")
    if live is not None:
        if not isinstance(live, dict):
            raise ConfigError("Section 'live' harus berupa mapping.")
        if live.get("camera") != "M4E_VIS":
            raise ConfigError("live.camera harus M4E_VIS.")
        if int(live.get("frame_queue_size", 0)) != 1:
            raise ConfigError("live.frame_queue_size harus 1.")
        if live.get("drop_old_frames") is not True:
            raise ConfigError("live.drop_old_frames harus true.")
        if int(live.get("batch_size", 0)) != 1:
            raise ConfigError("live.batch_size harus 1.")
        if float(live.get("target_inference_fps", 0)) <= 0:
            raise ConfigError("live.target_inference_fps harus positif.")
        if int(live.get("stream_timeout_ms", 0)) < 1000:
            raise ConfigError("live.stream_timeout_ms minimal 1000 ms.")
        if int(live.get("worker_heartbeat_timeout_ms", 0)) < 1000:
            raise ConfigError("live.worker_heartbeat_timeout_ms minimal 1000 ms.")

    logging_config = config["logging"]
    if logging_config.get("save_all_detections") is not True:
        raise ConfigError("logging.save_all_detections wajib true pada DEV.")
    if float(logging_config.get("sample_interval_seconds", 0)) <= 0:
        raise ConfigError("logging.sample_interval_seconds harus positif.")
