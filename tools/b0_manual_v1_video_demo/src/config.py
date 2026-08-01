from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "default_config.json"
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv"}


class ConfigurationError(ValueError):
    """Raised when a requested pipeline configuration is unsafe or invalid."""


def _deep_update(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def load_config(
    path: str | Path | None = None, overrides: dict[str, Any] | None = None
) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if overrides:
        config = _deep_update(deepcopy(config), overrides)
    for key in ("model_path", "artifact_dir", "input_dir", "video_path", "srt_path", "output_root"):
        value = config.get(key)
        if not value:
            continue
        candidate = Path(str(value)).expanduser()
        config[key] = str(candidate.resolve() if candidate.is_absolute() else (REPO_ROOT / candidate).resolve())
    validate_config(config)
    return config


def save_config(config: dict[str, Any], path: str | Path) -> None:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def validate_config(config: dict[str, Any]) -> None:
    model_path = Path(config["model_path"]).expanduser()
    if model_path.name not in {"best.pt", "plant_center_manual_v1_b0_best.pt"}:
        raise ConfigurationError(f"Model harus checkpoint B0 manual-v1 resmi, diterima: {model_path}")
    if not model_path.is_file():
        raise ConfigurationError(f"Model tidak ditemukan: {model_path}")
    if config["device"] not in {"auto", "mps", "cpu"}:
        raise ConfigurationError("Device harus auto, mps, atau cpu; CUDA tidak didukung.")
    tile_size = int(config["tile_size"])
    overlap = int(config["tile_overlap"])
    if tile_size <= 0 or overlap < 0 or overlap >= tile_size:
        raise ConfigurationError("tile_size harus > 0 dan overlap berada di [0, tile_size).")
    if int(config["image_size"]) != tile_size:
        raise ConfigurationError("image_size harus sama dengan tile_size untuk model ini.")
    if int(config["max_detections_per_tile"]) < 1000:
        raise ConfigurationError("max_detections_per_tile tidak boleh di bawah kontrak model 1000.")
    if int(config["max_detections_full_frame"]) <= 300:
        raise ConfigurationError("Batas full-frame harus > 300.")
    if config["roi_mode"] not in {"full_frame", "manual_rectangle", "dataset_derived"}:
        raise ConfigurationError("ROI mode tidak dikenal.")
    if config["playback_mode"] not in {"processed_frames_only", "continuous_video"}:
        raise ConfigurationError("Playback mode tidak dikenal.")
    for key in ("confidence_threshold", "nms_iou_threshold", "global_nms_iou"):
        value = float(config[key])
        if not 0.0 <= value <= 1.0:
            raise ConfigurationError(f"{key} harus berada di [0, 1].")


def discover_media_pair(input_dir: str | Path) -> tuple[Path, Path, dict[str, list[str]]]:
    root = Path(input_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Input directory tidak ditemukan: {root}")
    videos = sorted(path for path in root.rglob("*") if path.suffix.lower() in VIDEO_SUFFIXES)
    srts = sorted(path for path in root.rglob("*") if path.suffix.lower() == ".srt")
    candidates = {
        "videos": [str(path) for path in videos],
        "srts": [str(path) for path in srts],
    }
    if len(videos) == 1 and len(srts) == 1:
        return videos[0], srts[0], candidates
    matching = [
        (video, srt)
        for video in videos
        for srt in srts
        if video.stem.casefold() == srt.stem.casefold()
    ]
    if len(matching) == 1:
        return matching[0][0], matching[0][1], candidates
    raise ConfigurationError(
        "Pasangan video/SRT ambigu. Kandidat:\n" + json.dumps(candidates, indent=2)
    )


def resolve_roi(
    config: dict[str, Any], frame_width: int, frame_height: int
) -> tuple[int, int, int, int] | None:
    mode = config["roi_mode"]
    if mode == "full_frame":
        return None
    value = config["dataset_derived_roi"] if mode == "dataset_derived" else config.get("manual_roi")
    if not value:
        raise ConfigurationError(f"ROI untuk mode {mode} belum tersedia.")
    roi = (
        int(value["x"]),
        int(value["y"]),
        int(value["width"]),
        int(value["height"]),
    )
    x, y, width, height = roi
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ConfigurationError(f"ROI tidak valid: {roi}")
    if x + width > frame_width or y + height > frame_height:
        raise ConfigurationError(
            f"ROI {roi} keluar dari frame {frame_width}x{frame_height}."
        )
    return roi
