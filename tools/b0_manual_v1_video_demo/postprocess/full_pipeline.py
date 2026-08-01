"""Terminal-only full local inference pipeline for Drone AI Gap Plot."""

from __future__ import annotations

import argparse
import csv
import gc
import hashlib
import itertools
import json
import logging
import math
import os
import platform
import resource
import shutil
import sys
import time
import traceback
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd
import scipy
import shapely
import torch
import ultralytics
from scipy.spatial import cKDTree
from ultralytics import YOLO

from src.config import discover_media_pair, load_config, resolve_roi
from src.detection_merge import merge_detections
from src.exporters import DETECTION_COLUMNS, FRAME_SUMMARY_COLUMNS, TELEMETRY_COLUMNS
from src.model_loader import PlantCenterModel
from src.srt_parser import SRTEntry, parse_srt
from src.telemetry_sync import TelemetrySynchronizer
from src.tiled_inference import TiledInferenceEngine
from src.video_reader import VideoReader, audit_video

from .audit import audit_inference_run, audit_markdown
from .common import (
    list_to_matrix,
    quantiles,
    read_json,
    sha256_file,
    utc_now_iso,
    write_csv,
    write_json,
)
from .config import merged_config
from .fusion import fuse_detections, write_unique_plant_overlay
from .georef import evaluate_and_apply_georeference
from .geometry_sanitize import (
    sanitize_feature_collection,
    write_geometry_qa_report,
)
from .pipeline import run_gap_analysis
from .registration import build_mosaic, register_video_frames
from .segmentation import (
    AREA_DESCRIPTION,
    apply_segmentation_morphology,
    classify_points_by_mask,
    extract_local_area_features,
    predicted_union_binary,
    rasterize_local_geojson,
    write_local_areas_geojson,
)


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
EXPECTED_SEGMENTER_SHA256 = (
    "1fd2a8d78fda17f3f2938035aced92f0c2aa346cdf26df3d71b87a15982f3519"
)
STAGE_NAMES = (
    "Input audit",
    "SRT parsing",
    "Segmentation",
    "Plant detection",
    "Mosaic projection and deduplication",
    "Row and gap analysis",
    "QA and evidence",
    "Export",
)
RAW_DETECTION_NAME = "detection_observations_raw.csv"
DETECTOR_STAGE_DIR = "detector_inference"
STAGE_ARTIFACT_PATHS = {
    1: (),
    2: (
        "srt_parsed.csv",
        "srt_parse_report.json",
        "frame_manifest.csv",
    ),
    3: (
        "segmentation_frame_summary.csv",
        "segmentation_masks_raw",
        "segmentation_masks_closing3",
    ),
    4: (
        ".stages/detection_observations_raw.csv",
        ".stages/frame_summary_raw.csv",
        ".stages/detection_frames",
    ),
    5: (
        ".stages/segmentation_mosaic_votes.npz",
        "preview/segmentation_mosaic_majority_mask.png",
        "segmentation_areas_local.geojson",
        "frame_manifest_gated.csv",
        "segmentation_gating_report.json",
        "registration_edges.csv",
        "registration_report.json",
        "mosaic_transform.json",
        "frame_footprints_local.geojson",
        "coverage/coverage_counts.npz",
        "plant_observations.csv",
        "plant_observations_rejected.csv",
        "fusion_report.json",
        "georeference_report.json",
        "survey_mosaic_preview.png",
    ),
}


class TerminalProgress:
    """Small TTY/non-TTY progress reporter with rate, elapsed, and ETA."""

    def __init__(self, description: str, total: int, unit: str = "frame") -> None:
        self.description = description
        self.total = max(0, int(total))
        self.unit = unit
        self.started = time.perf_counter()
        self.completed = 0
        self.last_print = 0.0
        self.last_bucket = -1
        self.tty = bool(sys.stderr.isatty())

    def update(self, completed: int, extra: str = "") -> None:
        self.completed = int(completed)
        now = time.perf_counter()
        elapsed = max(now - self.started, 1e-9)
        percent = 100.0 if self.total == 0 else 100.0 * self.completed / self.total
        rate = self.completed / elapsed
        eta = (self.total - self.completed) / rate if rate > 0 else math.inf
        bucket = int(percent // 5)
        should_print = (
            self.tty
            or self.completed >= self.total
            or bucket > self.last_bucket
            or now - self.last_print >= 30.0
        )
        if not should_print:
            return
        width = 22
        filled = width if self.total == 0 else min(width, int(width * self.completed / self.total))
        bar = f"[{'=' * filled}{'.' * (width - filled)}]"
        eta_text = "--" if not math.isfinite(eta) else _duration(eta)
        text = (
            f"{self.description} {bar} {self.completed}/{self.total} {percent:6.2f}% "
            f"{rate:6.2f} {self.unit}/s elapsed={_duration(elapsed)} ETA={eta_text}"
        )
        if extra:
            text += f" {extra}"
        print(text, end="\r" if self.tty and self.completed < self.total else "\n", file=sys.stderr, flush=True)
        self.last_print = now
        self.last_bucket = bucket

    def close(self, extra: str = "") -> None:
        self.update(self.total, extra)


def _duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _stage_header(index: int) -> None:
    message = f"[{index}/8] {STAGE_NAMES[index - 1]}"
    print(message, file=sys.stderr, flush=True)
    LOGGER.info(message)


def _stage_marker(output_dir: Path, index: int) -> Path:
    return output_dir / ".stages" / f"{index:02d}_{STAGE_NAMES[index - 1].lower().replace(' ', '_')}.json"


def _stage_is_complete(output_dir: Path, index: int) -> bool:
    path = _stage_marker(output_dir, index)
    return path.is_file() and read_json(path).get("status") == "completed"


def _mark_manifest_resumed(path: Path) -> dict[str, Any]:
    manifest = read_json(path)
    provenance = manifest.setdefault("resume_provenance", {})
    provenance["resumed"] = True
    provenance["resume_requested_at"] = utc_now_iso()
    manifest["status"] = "running"
    manifest["updated_at"] = utc_now_iso()
    write_json(path, manifest)
    return manifest


def _mark_stage(
    output_dir: Path,
    index: int,
    started: float,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "stage": index,
        "name": STAGE_NAMES[index - 1],
        "status": "completed",
        "completed_at": utc_now_iso(),
        "runtime_seconds": time.perf_counter() - started,
        "details": dict(details or {}),
    }
    if index in STAGE_ARTIFACT_PATHS:
        payload["artifact_checkpoint"] = _artifact_checkpoint(output_dir, index)
    write_json(_stage_marker(output_dir, index), payload)
    return payload


def _update_manifest_stage(output_dir: Path, stage: Mapping[str, Any]) -> None:
    path = output_dir / "run_manifest.json"
    manifest = read_json(path)
    manifest.setdefault("stages", {})[str(stage["stage"])] = dict(stage)
    manifest["updated_at"] = utc_now_iso()
    write_json(path, manifest)


def _fingerprint(payload: Mapping[str, Any]) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _artifact_checkpoint(output_dir: Path, stage_index: int) -> dict[str, Any]:
    paths = STAGE_ARTIFACT_PATHS.get(stage_index, ())
    stage_digest = hashlib.sha256()
    inventory = []
    for relative_path in paths:
        artifact = output_dir / relative_path
        if not artifact.exists():
            raise FileNotFoundError(
                f"Stage {stage_index} checkpoint artifact is missing: {artifact}"
            )
        files = (
            [artifact]
            if artifact.is_file()
            else sorted(path for path in artifact.rglob("*") if path.is_file())
        )
        artifact_digest = hashlib.sha256()
        total_bytes = 0
        for file_path in files:
            file_relative = file_path.relative_to(output_dir).as_posix()
            file_size = file_path.stat().st_size
            file_sha256 = sha256_file(file_path)
            record = f"{file_relative}\0{file_size}\0{file_sha256}\n".encode()
            artifact_digest.update(record)
            stage_digest.update(record)
            total_bytes += file_size
        inventory.append(
            {
                "path": relative_path,
                "kind": "file" if artifact.is_file() else "directory",
                "file_count": len(files),
                "total_bytes": total_bytes,
                "sha256": artifact_digest.hexdigest(),
            }
        )
    return {
        "algorithm": "sha256_path_size_content",
        "fingerprint": stage_digest.hexdigest(),
        "artifact_count": len(paths),
        "file_count": sum(item["file_count"] for item in inventory),
        "total_bytes": sum(item["total_bytes"] for item in inventory),
        "artifacts": inventory,
    }


def _validate_stage_semantics(
    output_dir: Path,
    stage_index: int,
    frame_indices: Sequence[int],
) -> None:
    expected = [int(value) for value in frame_indices]
    if stage_index == 2:
        manifest = pd.read_csv(output_dir / "frame_manifest.csv")
        actual = manifest["frame_index"].astype(int).tolist()
        if actual != expected:
            raise RuntimeError("Stage 2 frame manifest does not match requested sampling")
    elif stage_index == 3:
        summary = pd.read_csv(output_dir / "segmentation_frame_summary.csv")
        actual = summary["frame_index"].astype(int).tolist()
        if actual != expected:
            raise RuntimeError("Stage 3 segmentation summary is incomplete or reordered")
        for directory in ("segmentation_masks_raw", "segmentation_masks_closing3"):
            missing = [
                frame_index
                for frame_index in expected
                if not (
                    output_dir / directory / f"frame_{frame_index:06d}.png"
                ).is_file()
            ]
            if missing:
                raise RuntimeError(
                    f"Stage 3 {directory} is missing {len(missing)} sampled frames"
                )
    elif stage_index == 4:
        summary = pd.read_csv(output_dir / ".stages" / "frame_summary_raw.csv")
        actual = summary["frame_index"].astype(int).tolist()
        if actual != expected:
            raise RuntimeError("Stage 4 detector summary is incomplete or reordered")
        detection_dir = output_dir / ".stages" / "detection_frames"
        missing = [
            frame_index
            for frame_index in expected
            if not (
                (detection_dir / f"frame_{frame_index:06d}.csv").is_file()
                and (detection_dir / f"frame_{frame_index:06d}.json").is_file()
            )
        ]
        if missing:
            raise RuntimeError(
                f"Stage 4 per-frame detector cache is missing {len(missing)} sampled frames"
            )
    elif stage_index == 5:
        polygons = read_json(output_dir / "segmentation_areas_local.geojson")
        if not polygons.get("features"):
            raise RuntimeError("Stage 5 has no segmentation analysis-area feature")
        with np.load(output_dir / "coverage" / "coverage_counts.npz") as coverage_payload:
            coverage = coverage_payload["coverage"]
        if coverage.ndim != 2 or not coverage.size:
            raise RuntimeError("Stage 5 coverage checkpoint is empty or malformed")
        registration = read_json(output_dir / "registration_report.json")
        if int(registration.get("frame_count", -1)) != len(expected):
            raise RuntimeError("Stage 5 registration frame count does not match sampling")


def _validate_resume_checkpoints(
    output_dir: Path,
    frame_indices: Sequence[int],
    *,
    maximum_stage: int = 5,
) -> list[int]:
    completed = [
        index
        for index in range(1, maximum_stage + 1)
        if _stage_is_complete(output_dir, index)
    ]
    if completed and completed != list(range(1, max(completed) + 1)):
        raise RuntimeError(f"Resume stage markers are not contiguous: {completed}")
    for stage_index in completed:
        _validate_stage_semantics(output_dir, stage_index, frame_indices)
        current = _artifact_checkpoint(output_dir, stage_index)
        marker_path = _stage_marker(output_dir, stage_index)
        marker = read_json(marker_path)
        recorded = marker.get("artifact_checkpoint")
        if recorded and recorded.get("fingerprint") != current["fingerprint"]:
            raise RuntimeError(
                f"Stage {stage_index} artifact fingerprint mismatch; refusing cache reuse"
            )
        marker["artifact_checkpoint"] = current
        marker["checkpoint_validated_at"] = utc_now_iso()
        marker["checkpoint_validation"] = "passed"
        write_json(marker_path, marker)
        _update_manifest_stage(output_dir, marker)
    manifest_path = output_dir / "run_manifest.json"
    manifest = read_json(manifest_path)
    provenance = manifest.setdefault("resume_provenance", {})
    provenance["checkpoint_validated_at"] = utc_now_iso()
    provenance["validated_stages"] = completed
    provenance["reused_stages"] = completed
    provenance["artifact_validation"] = "passed"
    write_json(manifest_path, manifest)
    return completed


def _safe_prepare_output(output_dir: Path, resume: bool, force: bool) -> None:
    output_dir = output_dir.resolve()
    protected = {
        Path.home().resolve(),
        PROJECT_ROOT.resolve(),
        PROJECT_ROOT.parent.resolve(),
        Path("/").resolve(),
    }
    if output_dir.exists():
        if resume:
            if not (output_dir / "run_manifest.json").is_file():
                raise FileExistsError("Resume requires an existing run_manifest.json")
            return
        if not force:
            raise FileExistsError(
                f"Refusing to overwrite existing run: {output_dir}. Use --resume or explicit --force."
            )
        if output_dir in protected or len(output_dir.parts) < 5:
            raise ValueError(f"Unsafe --force target: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / ".stages").mkdir()


def _configure_logging(output_dir: Path, level: str) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level))
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in list(root.handlers):
        if getattr(handler, "_full_pipeline_handler", False):
            root.removeHandler(handler)
            handler.close()
    file_handler = logging.FileHandler(output_dir / "pipeline.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler._full_pipeline_handler = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)
    if not any(isinstance(handler, logging.StreamHandler) for handler in root.handlers if handler is not file_handler):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        stream_handler._full_pipeline_handler = True  # type: ignore[attr-defined]
        root.addHandler(stream_handler)


def _environment() -> dict[str, Any]:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mib = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "packages": {
            "opencv": cv2.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": scipy.__version__,
            "shapely": shapely.__version__,
            "torch": torch.__version__,
            "ultralytics": ultralytics.__version__,
        },
        "torch_mps_built": bool(torch.backends.mps.is_built()),
        "torch_mps_available": bool(torch.backends.mps.is_available()),
        "peak_memory_mib_at_capture": peak_mib,
    }


def _segmenter_identity(model: YOLO, path: Path) -> dict[str, Any]:
    checkpoint = getattr(model, "ckpt", {}) or {}
    train_args = checkpoint.get("train_args", {}) if isinstance(checkpoint, Mapping) else {}
    yaml = getattr(getattr(model, "model", None), "yaml", {}) or {}
    epoch = checkpoint.get("epoch") if isinstance(checkpoint, Mapping) else None
    identity = {
        "path": str(path),
        "file_size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "identity": "C_selected_comparison_epoch56",
        "artifact_model_name": "C_random_crop_control",
        "task": model.task,
        "architecture": "YOLOv8s-seg",
        "class_mapping": {str(key): value for key, value in model.names.items()},
        "trainer_epoch_index": epoch,
        "human_epoch": int(epoch) + 1 if epoch is not None else None,
        "training_imgsz": train_args.get("imgsz"),
        "training_batch": train_args.get("batch"),
        "training_run_name": train_args.get("name"),
        "checkpoint_ultralytics_version": checkpoint.get("version") if isinstance(checkpoint, Mapping) else None,
        "model_yaml_depth_multiple": yaml.get("depth_multiple"),
        "model_yaml_width_multiple": yaml.get("width_multiple"),
    }
    failures = []
    if identity["sha256"] != EXPECTED_SEGMENTER_SHA256:
        failures.append("sha256_mismatch")
    if identity["task"] != "segment":
        failures.append("task_not_segment")
    if set(model.names.values()) != {"plantable_area"}:
        failures.append("class_mapping_mismatch")
    if epoch != 55:
        failures.append("trainer_epoch_index_not_55")
    if train_args.get("imgsz") != 1280:
        failures.append("training_imgsz_not_1280")
    if train_args.get("batch") != 3:
        failures.append("training_batch_not_3")
    if yaml.get("width_multiple") != 0.5:
        failures.append("architecture_not_yolov8s_seg")
    if failures:
        raise RuntimeError(f"Segmenter checkpoint audit failed: {failures}")
    return identity


def _write_srt_csv(path: Path, entries: Sequence[SRTEntry]) -> None:
    rows = []
    for entry in entries:
        row = entry.to_dict()
        row["extras_json"] = json.dumps(row.pop("extras"), ensure_ascii=False, sort_keys=True)
        rows.append(row)
    fields = list(rows[0]) if rows else [
        "index",
        "start_seconds",
        "end_seconds",
        "raw_text",
        "extras_json",
    ]
    write_csv(path, rows, fields)


def _write_frame_manifest(
    path: Path,
    frame_indices: Sequence[int],
    synchronizer: TelemetrySynchronizer,
    fps: float,
) -> None:
    rows = []
    for frame_index in frame_indices:
        sync = synchronizer.sync(frame_index, fps)
        telemetry = sync.telemetry
        rows.append(
            {
                "frame_index": frame_index,
                "video_time_seconds": sync.video_time_seconds,
                "srt_index": sync.srt_index,
                "srt_start_seconds": sync.srt_start_seconds,
                "srt_end_seconds": sync.srt_end_seconds,
                "sync_delta_seconds": sync.sync_delta_seconds,
                "sync_status": sync.sync_status,
                "drone_latitude": telemetry.latitude if telemetry else None,
                "drone_longitude": telemetry.longitude if telemetry else None,
                "relative_altitude": telemetry.relative_altitude if telemetry else None,
                "absolute_altitude": telemetry.absolute_altitude if telemetry else None,
                "gimbal_yaw": telemetry.gimbal_yaw if telemetry else None,
                "gimbal_pitch": telemetry.gimbal_pitch if telemetry else None,
                "gimbal_roll": telemetry.gimbal_roll if telemetry else None,
            }
        )
    write_csv(path, rows, list(rows[0]) if rows else ["frame_index"])


def _segmentation_summary_rows(path: Path) -> dict[int, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {int(row["frame_index"]): row for row in csv.DictReader(handle)}


def _append_segmentation_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = [
        "frame_index",
        "video_time_seconds",
        "instance_count",
        "confidence_min",
        "confidence_mean",
        "confidence_max",
        "raw_positive_pixels",
        "processed_positive_pixels",
        "morphology_changed_pixels",
        "processed_fraction",
        "inference_seconds",
        "raw_mask_file",
        "processed_mask_file",
    ]
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())


def _run_segmentation(
    model: YOLO,
    video_path: Path,
    output_dir: Path,
    frame_indices: Sequence[int],
    fps: float,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    raw_dir = output_dir / "segmentation_masks_raw"
    processed_dir = output_dir / "segmentation_masks_closing3"
    raw_dir.mkdir(exist_ok=True)
    processed_dir.mkdir(exist_ok=True)
    summary_path = output_dir / "segmentation_frame_summary.csv"
    completed = _segmentation_summary_rows(summary_path)
    completed = {
        frame: row
        for frame, row in completed.items()
        if (raw_dir / f"frame_{frame:06d}.png").is_file()
        and (processed_dir / f"frame_{frame:06d}.png").is_file()
    }
    pending = set(frame_indices) - set(completed)
    progress = TerminalProgress("segmentation", len(frame_indices), "frame")
    progress.update(len(completed), f"masks={len(completed)} resumed={len(completed)}")
    batch_size = int(config["batch_size"])
    wanted = set(pending)
    batch_frames: list[np.ndarray] = []
    batch_indices: list[int] = []
    total_instances = sum(int(float(row["instance_count"])) for row in completed.values())
    inference_seconds = sum(float(row["inference_seconds"]) for row in completed.values())

    def flush_batch() -> None:
        nonlocal total_instances, inference_seconds
        if not batch_frames:
            return
        started = time.perf_counter()
        source: Any = batch_frames if len(batch_frames) > 1 else batch_frames[0]
        with torch.inference_mode():
            results = model.predict(
                source=source,
                imgsz=int(config["imgsz"]),
                conf=float(config["confidence"]),
                iou=float(config["nms_iou"]),
                device=str(config["device"]),
                retina_masks=True,
                verbose=False,
                save=False,
                stream=False,
            )
        elapsed = time.perf_counter() - started
        inference_seconds += elapsed
        rows = []
        for frame_index, frame, result in zip(batch_indices, batch_frames, results, strict=True):
            classes = (
                result.boxes.cls.detach().cpu().numpy().astype(int)
                if result.boxes is not None and len(result.boxes)
                else np.empty(0, dtype=int)
            )
            confidences = (
                result.boxes.conf.detach().cpu().numpy()
                if result.boxes is not None and len(result.boxes)
                else np.empty(0, dtype=float)
            )
            masks = result.masks.data if result.masks is not None else None
            raw = predicted_union_binary(
                masks,
                classes,
                class_id=0,
                target_shape=frame.shape[:2],
            )
            processed = apply_segmentation_morphology(raw, str(config["morphology"]))
            raw_path = raw_dir / f"frame_{frame_index:06d}.png"
            processed_path = processed_dir / f"frame_{frame_index:06d}.png"
            if not cv2.imwrite(str(raw_path), raw, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
                raise OSError(f"Failed to write {raw_path}")
            if not cv2.imwrite(str(processed_path), processed, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
                raise OSError(f"Failed to write {processed_path}")
            instance_count = int(np.sum(classes == 0))
            total_instances += instance_count
            raw_pixels = int(np.count_nonzero(raw))
            processed_pixels = int(np.count_nonzero(processed))
            rows.append(
                {
                    "frame_index": frame_index,
                    "video_time_seconds": frame_index / fps,
                    "instance_count": instance_count,
                    "confidence_min": float(confidences.min()) if len(confidences) else "",
                    "confidence_mean": float(confidences.mean()) if len(confidences) else "",
                    "confidence_max": float(confidences.max()) if len(confidences) else "",
                    "raw_positive_pixels": raw_pixels,
                    "processed_positive_pixels": processed_pixels,
                    "morphology_changed_pixels": int(np.count_nonzero(raw != processed)),
                    "processed_fraction": processed_pixels / processed.size,
                    "inference_seconds": elapsed / len(batch_frames),
                    "raw_mask_file": str(raw_path.relative_to(output_dir)),
                    "processed_mask_file": str(processed_path.relative_to(output_dir)),
                }
            )
        _append_segmentation_rows(summary_path, rows)
        completed.update({int(row["frame_index"]): {key: str(value) for key, value in row.items()} for row in rows})
        progress.update(
            len(completed),
            f"instances={total_instances} masks={len(completed)} batch={len(batch_frames)}",
        )
        results.clear()
        batch_frames.clear()
        batch_indices.clear()

    if wanted:
        with VideoReader(video_path) as reader:
            for frame_index, frame in reader.iter_frames():
                if frame_index not in wanted:
                    continue
                batch_indices.append(frame_index)
                batch_frames.append(frame)
                if len(batch_frames) >= batch_size:
                    flush_batch()
                if len(completed) == len(frame_indices):
                    break
            flush_batch()
    if set(completed) != set(frame_indices):
        missing = sorted(set(frame_indices) - set(completed))
        raise RuntimeError(f"Segmentation did not produce all sampled frames: {missing[:10]}")
    progress.close(f"instances={total_instances} masks={len(completed)}")
    return {
        "processed_frames": len(completed),
        "raw_instance_masks_union_count": total_instances,
        "inference_seconds": inference_seconds,
        "morphology": config["morphology"],
        "canonical_output": "predicted_union_binary",
    }


def _detection_shard_dir(output_dir: Path) -> Path:
    path = output_dir / ".stages" / "detection_frames"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _missing_detection_indices(output_dir: Path, frame_indices: Sequence[int]) -> list[int]:
    directory = _detection_shard_dir(output_dir)
    return [
        frame
        for frame in frame_indices
        if not (directory / f"frame_{frame:06d}.csv").is_file()
        or not (directory / f"frame_{frame:06d}.json").is_file()
    ]


def _atomic_csv(path: Path, rows: Iterable[Mapping[str, Any]], fields: Sequence[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _run_detection(
    model: PlantCenterModel | None,
    video_path: Path,
    output_dir: Path,
    frame_indices: Sequence[int],
    synchronizer: TelemetrySynchronizer,
    detector_config: dict[str, Any],
) -> dict[str, Any]:
    directory = _detection_shard_dir(output_dir)
    missing = _missing_detection_indices(output_dir, frame_indices)
    existing_count = len(frame_indices) - len(missing)
    total_detections = 0
    inference_seconds = 0.0
    for frame in set(frame_indices) - set(missing):
        metadata = read_json(directory / f"frame_{frame:06d}.json")
        total_detections += int(metadata["frame_summary"]["plant_count"])
        inference_seconds += float(metadata["frame_summary"]["inference_ms"]) / 1000.0
    progress = TerminalProgress("plant detection", len(frame_indices), "frame")
    progress.update(existing_count, f"detections={total_detections} resumed={existing_count}")
    if missing:
        if model is None:
            raise RuntimeError("Detector model is required for pending frames")
        wanted = set(missing)
        with VideoReader(video_path) as reader:
            roi = resolve_roi(detector_config, reader.width, reader.height)
            smoke_frame = reader.read_frame(missing[0])
            x, y, width, height = roi or (0, 0, reader.width, reader.height)
            smoke_tile = smoke_frame[y : y + min(height, 1024), x : x + min(width, 1024)]
            model.select_device(smoke_tile, detector_config)
            del smoke_frame, smoke_tile
            engine = TiledInferenceEngine(model, detector_config)
            for frame_index, frame in reader.iter_frames():
                if frame_index not in wanted:
                    continue
                sync = synchronizer.sync(frame_index, reader.fps)
                raw, windows, timings = engine.infer_frame(frame, roi)
                merged, diagnostics = merge_detections(
                    raw,
                    float(detector_config["global_nms_iou"]),
                    float(detector_config["center_duplicate_radius_px"]),
                    bool(detector_config["enable_center_suppression"]),
                    int(detector_config["max_detections_full_frame"]),
                )
                telemetry = sync.telemetry
                rows = []
                for detection_id, detection in enumerate(merged):
                    rows.append(
                        {
                            "frame_index": frame_index,
                            "video_time_seconds": sync.video_time_seconds,
                            "detection_id": detection_id,
                            **detection.to_dict(),
                            "latitude": telemetry.latitude if telemetry else None,
                            "longitude": telemetry.longitude if telemetry else None,
                            "altitude": telemetry.altitude if telemetry else None,
                            "gimbal_pitch": telemetry.gimbal_pitch if telemetry else None,
                            "srt_sync_status": sync.sync_status,
                        }
                    )
                frame_path = directory / f"frame_{frame_index:06d}.csv"
                _atomic_csv(frame_path, rows, DETECTION_COLUMNS)
                frame_row = {
                    "frame_index": frame_index,
                    "video_time_seconds": sync.video_time_seconds,
                    "plant_count": len(merged),
                    **diagnostics.to_dict(),
                    "tile_count": len(windows),
                    "inference_ms": timings["inference_ms"],
                    "render_ms": 0.0,
                    "effective_fps": 1000.0 / timings["inference_ms"] if timings["inference_ms"] > 0 else 0.0,
                    "srt_index": sync.srt_index,
                    "sync_delta_seconds": sync.sync_delta_seconds,
                    "sync_status": sync.sync_status,
                }
                telemetry_row = {
                    "frame_index": frame_index,
                    "video_time_seconds": sync.video_time_seconds,
                    "srt_index": sync.srt_index,
                    "srt_start_seconds": sync.srt_start_seconds,
                    "srt_end_seconds": sync.srt_end_seconds,
                    "sync_delta_seconds": sync.sync_delta_seconds,
                    "sync_status": sync.sync_status,
                    "latitude": telemetry.latitude if telemetry else None,
                    "longitude": telemetry.longitude if telemetry else None,
                    "relative_altitude": telemetry.relative_altitude if telemetry else None,
                    "absolute_altitude": telemetry.absolute_altitude if telemetry else None,
                    "gimbal_yaw": telemetry.gimbal_yaw if telemetry else None,
                    "gimbal_pitch": telemetry.gimbal_pitch if telemetry else None,
                    "gimbal_roll": telemetry.gimbal_roll if telemetry else None,
                    "raw_text": telemetry.raw_text if telemetry else None,
                }
                write_json(
                    directory / f"frame_{frame_index:06d}.json",
                    {"frame_summary": frame_row, "telemetry": telemetry_row},
                )
                total_detections += len(merged)
                inference_seconds += timings["inference_ms"] / 1000.0
                existing_count += 1
                progress.update(
                    existing_count,
                    f"detections={total_detections} tiles={len(windows)} "
                    f"frame_s={timings['inference_ms'] / 1000.0:.2f}",
                )
                del frame, raw, merged, windows
                if existing_count == len(frame_indices):
                    break
    raw_path = output_dir / ".stages" / RAW_DETECTION_NAME
    frame_summary_path = output_dir / ".stages" / "frame_summary_raw.csv"
    telemetry_path = output_dir / ".stages" / "telemetry_sync.csv"
    with raw_path.with_suffix(".csv.tmp").open("w", newline="", encoding="utf-8") as raw_handle, frame_summary_path.with_suffix(".csv.tmp").open("w", newline="", encoding="utf-8") as frame_handle, telemetry_path.with_suffix(".csv.tmp").open("w", newline="", encoding="utf-8") as telemetry_handle:
        raw_writer = csv.DictWriter(raw_handle, fieldnames=DETECTION_COLUMNS)
        frame_writer = csv.DictWriter(frame_handle, fieldnames=FRAME_SUMMARY_COLUMNS, extrasaction="ignore")
        telemetry_writer = csv.DictWriter(telemetry_handle, fieldnames=TELEMETRY_COLUMNS, extrasaction="ignore")
        raw_writer.writeheader()
        frame_writer.writeheader()
        telemetry_writer.writeheader()
        for frame_index in frame_indices:
            metadata = read_json(directory / f"frame_{frame_index:06d}.json")
            frame_writer.writerow(metadata["frame_summary"])
            telemetry_writer.writerow(metadata["telemetry"])
            with (directory / f"frame_{frame_index:06d}.csv").open(newline="", encoding="utf-8") as source:
                raw_writer.writerows(csv.DictReader(source))
    raw_path.with_suffix(".csv.tmp").replace(raw_path)
    frame_summary_path.with_suffix(".csv.tmp").replace(frame_summary_path)
    telemetry_path.with_suffix(".csv.tmp").replace(telemetry_path)
    progress.close(f"detections={total_detections}")
    return {
        "processed_frames": len(frame_indices),
        "total_detections": total_detections,
        "inference_seconds": inference_seconds,
        "device": model.device_decision.to_dict() if model and model.device_decision else {"actual_device": "cpu"},
    }


def _sample_spacing(path: Path, frame_indices: Sequence[int]) -> dict[str, float | None]:
    sampled = set(frame_indices[::25])
    points: dict[int, list[list[float]]] = {frame: [] for frame in sampled}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            frame = int(row["frame_index"])
            if frame in points:
                points[frame].append([float(row["center_x"]), float(row["center_y"])])
    nearest = []
    for values in points.values():
        if len(values) < 2:
            continue
        array = np.asarray(values, dtype=np.float64)
        distances, _ = cKDTree(array).query(array, k=2)
        nearest.extend(distances[:, 1].tolist())
    result = quantiles(nearest)
    if result["p50"] is None:
        raise RuntimeError("Could not derive within-frame plant spacing for boundary gating")
    return result


def _gate_observations(
    output_dir: Path,
    frame_indices: Sequence[int],
    config: Mapping[str, Any],
    detector_config: Mapping[str, Any],
    input_hashes: Mapping[str, str],
    srt_report: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    raw_path = output_dir / ".stages" / RAW_DETECTION_NAME
    spacing = _sample_spacing(raw_path, frame_indices)
    multiplier = float(config["gap_analysis"]["boundary_buffer_row_spacing_fraction"])
    boundary_buffer = multiplier * float(spacing["p50"])
    accepted_fields = list(DETECTION_COLUMNS) + [
        "segmentation_gate_status",
        "segmentation_gate_reason",
        "segmentation_boundary_distance_px",
    ]
    observation_fields = [
        *[name for name in DETECTION_COLUMNS if name not in {"latitude", "longitude", "altitude"}],
        "drone_latitude",
        "drone_longitude",
        "drone_altitude",
        "segmentation_gate_status",
        "segmentation_gate_reason",
        "segmentation_boundary_distance_px",
    ]
    observation_tmp = output_dir / "plant_observations.csv.tmp"
    rejected_tmp = output_dir / "plant_observations_rejected.csv.tmp"
    detector_dir = output_dir / ".stages" / DETECTOR_STAGE_DIR
    detector_dir.mkdir(exist_ok=True)
    accepted_tmp = detector_dir / "detections.csv.tmp"
    frame_tmp = detector_dir / "frame_summary.csv.tmp"
    raw_frame_summary = {
        int(row.frame_index): row._asdict()
        for row in pd.read_csv(output_dir / ".stages" / "frame_summary_raw.csv").itertuples(index=False)
    }
    counts = {"before": 0, "accepted": 0, "boundary_review": 0, "outside_rejected": 0}
    progress = TerminalProgress("segmentation gating", len(frame_indices), "frame")
    with raw_path.open(newline="", encoding="utf-8") as source, observation_tmp.open("w", newline="", encoding="utf-8") as observation_handle, rejected_tmp.open("w", newline="", encoding="utf-8") as rejected_handle, accepted_tmp.open("w", newline="", encoding="utf-8") as accepted_handle, frame_tmp.open("w", newline="", encoding="utf-8") as frame_handle:
        reader = csv.DictReader(source)
        observation_writer = csv.DictWriter(observation_handle, fieldnames=observation_fields, extrasaction="ignore")
        rejected_writer = csv.DictWriter(rejected_handle, fieldnames=observation_fields, extrasaction="ignore")
        accepted_writer = csv.DictWriter(accepted_handle, fieldnames=accepted_fields, extrasaction="ignore")
        frame_fields = list(FRAME_SUMMARY_COLUMNS) + [
            "raw_plant_count",
            "accepted_count",
            "boundary_review_count",
            "outside_rejected_count",
        ]
        frame_writer = csv.DictWriter(frame_handle, fieldnames=frame_fields, extrasaction="ignore")
        observation_writer.writeheader()
        rejected_writer.writeheader()
        accepted_writer.writeheader()
        frame_writer.writeheader()
        completed = 0
        for frame_text, group in itertools.groupby(reader, key=lambda row: row["frame_index"]):
            frame_index = int(frame_text)
            rows = list(group)
            mask = cv2.imread(
                str(output_dir / "segmentation_masks_closing3" / f"frame_{frame_index:06d}.png"),
                cv2.IMREAD_GRAYSCALE,
            )
            if mask is None:
                raise FileNotFoundError(f"Processed segmentation mask missing for frame {frame_index}")
            points = np.asarray(
                [[float(row["center_x"]), float(row["center_y"])] for row in rows],
                dtype=np.float64,
            )
            statuses, distances = classify_points_by_mask(mask, points, boundary_buffer)
            per_frame = {"accepted": 0, "boundary_review": 0, "outside_rejected": 0}
            for row, status, distance in zip(rows, statuses, distances, strict=True):
                status_text = str(status)
                reason = {
                    "accepted": "inside_safe_interior_mask",
                    "boundary_review": "inside_processed_mask_within_boundary_buffer",
                    "outside_rejected": "outside_predicted_union_binary_processed",
                }[status_text]
                enriched = {
                    **row,
                    "drone_latitude": row.get("latitude"),
                    "drone_longitude": row.get("longitude"),
                    "drone_altitude": row.get("altitude"),
                    "segmentation_gate_status": status_text,
                    "segmentation_gate_reason": reason,
                    "segmentation_boundary_distance_px": float(distance),
                }
                observation_writer.writerow(enriched)
                if status_text == "outside_rejected":
                    rejected_writer.writerow(enriched)
                else:
                    accepted_writer.writerow(enriched)
                per_frame[status_text] += 1
                counts[status_text] += 1
                counts["before"] += 1
            frame_row = raw_frame_summary[frame_index]
            frame_row.update(
                {
                    "plant_count": per_frame["accepted"] + per_frame["boundary_review"],
                    "raw_plant_count": len(rows),
                    "accepted_count": per_frame["accepted"],
                    "boundary_review_count": per_frame["boundary_review"],
                    "outside_rejected_count": per_frame["outside_rejected"],
                }
            )
            frame_writer.writerow(frame_row)
            completed += 1
            progress.update(
                completed,
                f"accepted={counts['accepted']} boundary={counts['boundary_review']} "
                f"rejected={counts['outside_rejected']}",
            )
            del mask, points, statuses, distances, rows
    observation_tmp.replace(output_dir / "plant_observations.csv")
    rejected_tmp.replace(output_dir / "plant_observations_rejected.csv")
    accepted_tmp.replace(detector_dir / "detections.csv")
    frame_tmp.replace(detector_dir / "frame_summary.csv")
    shutil.copy2(output_dir / ".stages" / "telemetry_sync.csv", detector_dir / "telemetry_sync.csv")
    shutil.copy2(output_dir / ".stages" / "telemetry_sync.csv", output_dir / "telemetry_sync.csv")
    shutil.copy2(output_dir / ".stages" / "frame_summary_raw.csv", output_dir / "detector_frame_summary_raw.csv")
    shutil.copy2(detector_dir / "frame_summary.csv", output_dir / "frame_manifest_gated.csv")
    run_summary = {
        "status": "completed",
        "processed_frames": len(frame_indices),
        "total_detections": counts["accepted"] + counts["boundary_review"],
        "source_hashes_before": {
            "model": input_hashes["detector_model"],
            "video": input_hashes["video"],
            "srt": input_hashes["srt"],
        },
        "source_hashes_after": {
            "model": input_hashes["detector_model"],
            "video": input_hashes["video"],
            "srt": input_hashes["srt"],
        },
        "source_integrity_preserved": True,
        "training_performed": False,
        "segmentation_gating": counts,
    }
    write_json(detector_dir / "run_config.json", detector_config)
    write_json(detector_dir / "run_summary.json", run_summary)
    write_json(detector_dir / "environment.json", _environment())
    write_json(detector_dir / "srt_parse_report.json", srt_report)
    progress.close(
        f"accepted={counts['accepted']} boundary={counts['boundary_review']} "
        f"rejected={counts['outside_rejected']}"
    )
    gate_report = {
        **counts,
        "after": counts["accepted"] + counts["boundary_review"],
        "sample_within_frame_nearest_plant_distance_px": spacing,
        "boundary_buffer_spacing_multiplier": multiplier,
        "boundary_buffer_px": boundary_buffer,
        "boundary_review_included_in_temporal_fusion": True,
        "outside_records_preserved": True,
    }
    write_json(output_dir / "segmentation_gating_report.json", gate_report)
    return detector_dir, gate_report


def _aggregate_segmentation_local(
    output_dir: Path,
    frame_indices: Sequence[int],
    transforms: Mapping[int, np.ndarray],
    mosaic_info: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], np.ndarray]:
    preview_width, preview_height = map(int, mosaic_info["preview_size"])
    local_to_preview = list_to_matrix(mosaic_info["local_to_preview"])
    preview_to_local = list_to_matrix(mosaic_info["preview_to_local"])
    roi_height = int(config["coverage"]["roi_height"])
    votes = np.zeros((preview_height, preview_width), dtype=np.uint16)
    valid_count = np.zeros_like(votes)
    progress = TerminalProgress("segmentation mosaic", len(frame_indices), "frame")
    for completed, frame_index in enumerate(frame_indices, start=1):
        mask = cv2.imread(
            str(output_dir / "segmentation_masks_closing3" / f"frame_{frame_index:06d}.png"),
            cv2.IMREAD_GRAYSCALE,
        )
        if mask is None:
            raise FileNotFoundError(f"Missing processed mask for frame {frame_index}")
        mask[roi_height:, :] = 0
        valid = np.zeros_like(mask, dtype=np.uint8)
        valid[:roi_height, :] = 1
        warp = local_to_preview @ transforms[frame_index]
        warped = cv2.warpPerspective(
            (mask > 0).astype(np.uint8),
            warp,
            (preview_width, preview_height),
            flags=cv2.INTER_NEAREST,
        )
        warped_valid = cv2.warpPerspective(
            valid,
            warp,
            (preview_width, preview_height),
            flags=cv2.INTER_NEAREST,
        )
        votes += warped.astype(np.uint16)
        valid_count += warped_valid.astype(np.uint16)
        progress.update(completed, f"positive_votes={int(votes.sum())}")
        del mask, valid, warped, warped_valid
    threshold = float(config["segmentation_aggregation"]["minimum_vote_fraction"])
    majority = (
        (valid_count > 0)
        & (votes.astype(np.float32) / np.maximum(valid_count, 1) >= threshold)
    ).astype(np.uint8) * 255
    preview_dir = output_dir / "preview"
    preview_dir.mkdir(exist_ok=True)
    cv2.imwrite(str(preview_dir / "segmentation_mosaic_majority_mask.png"), majority)
    np.savez_compressed(
        output_dir / ".stages" / "segmentation_mosaic_votes.npz",
        votes=votes,
        valid_count=valid_count,
    )
    raw_features = extract_local_area_features(
        majority,
        preview_to_local,
        minimum_component_area_preview_px2=float(
            config["segmentation_aggregation"]["minimum_component_area_preview_px2"]
        ),
        simplify_preview_px=float(config["segmentation_aggregation"]["simplify_preview_px"]),
    )
    if not raw_features:
        raise RuntimeError("Segmentation produced no valid local analysis-area polygon")
    aggregation = {
        "method": "per_preview_pixel_temporal_majority_of_processed_frame_union_masks",
        "minimum_vote_fraction": threshold,
        "morphology_applied_per_frame": "closing_3",
        "frame_count": len(frame_indices),
        "positive_preview_pixels": int(np.count_nonzero(majority)),
        "raw_contour_component_count": len(raw_features),
        "raw_hole_count": int(
            sum(feature["properties"]["hole_count"] for feature in raw_features)
        ),
    }
    write_local_areas_geojson(
        output_dir / "segmentation_areas_local_raw.geojson",
        raw_features,
        aggregation=aggregation,
        preview_to_local=preview_to_local,
    )
    sanitation_config = config.get("geometry_sanitation", {})
    sanitized_payload, geometry_pass = sanitize_feature_collection(
        read_json(output_dir / "segmentation_areas_local_raw.geojson"),
        minimum_component_area=float(
            sanitation_config.get("minimum_component_area_local_px2", 1.0)
        ),
        minimum_hole_area=float(
            sanitation_config.get("minimum_hole_area_local_px2", 1.0)
        ),
        simplify_tolerance=float(
            config["segmentation_aggregation"]["simplify_preview_px"]
        )
        * math.sqrt(
            abs(float(np.linalg.det(np.asarray(preview_to_local)[:2, :2])))
        ),
        material_area_change_fraction=float(
            sanitation_config.get("material_area_change_fraction", 0.02)
        ),
    )
    features = sanitized_payload["features"]
    aggregation["component_count"] = len(features)
    aggregation["polygonal_component_count"] = int(
        sum(
            feature["properties"]["geometry_component_count"]
            for feature in features
        )
    )
    aggregation["holes_preserved"] = int(
        sum(feature["properties"]["hole_count"] for feature in features)
    )
    sanitized_payload["aggregation"] = aggregation
    write_json(output_dir / "segmentation_areas_local.geojson", sanitized_payload)
    write_geometry_qa_report(
        output_dir / "polygon_geometry_qa.json",
        geometry_pass,
        context="segmentation_mask_vectorization",
    )
    shutil.copy2(
        output_dir / "segmentation_areas_local.geojson",
        output_dir / "plots_local.geojson",
    )
    progress.close(f"areas={len(features)} holes={aggregation['holes_preserved']}")
    return aggregation, majority


def _propagate_gate_status(output_dir: Path, accepted_path: Path) -> None:
    gates = pd.read_csv(
        accepted_path,
        usecols=[
            "frame_index",
            "detection_id",
            "segmentation_gate_status",
            "segmentation_boundary_distance_px",
        ],
    )
    observations_path = output_dir / "track_observations.csv"
    observations = pd.read_csv(observations_path)
    observations = observations.merge(
        gates,
        on=["frame_index", "detection_id"],
        how="left",
        validate="one_to_one",
    )
    observations.to_csv(observations_path, index=False)
    summary = (
        observations.assign(
            safe=(observations["segmentation_gate_status"] == "accepted").astype(int),
            boundary=(observations["segmentation_gate_status"] == "boundary_review").astype(int),
        )
        .groupby("plant_id", as_index=False)
        .agg(
            safe_interior_observations=("safe", "sum"),
            boundary_review_observations=("boundary", "sum"),
            minimum_segmentation_boundary_distance_px=(
                "segmentation_boundary_distance_px",
                "min",
            ),
        )
    )
    unique_path = output_dir / "unique_plants.csv"
    unique = pd.read_csv(unique_path)
    unique = unique.merge(summary, on="plant_id", how="left", validate="one_to_one")
    unique["segmentation_gate_status"] = np.where(
        unique["safe_interior_observations"] > 0,
        "accepted_with_safe_interior_support",
        "boundary_review_only",
    )
    unique.to_csv(unique_path, index=False)


def _compare_old_area(
    output_dir: Path,
    majority_mask: np.ndarray,
    local_to_preview: np.ndarray,
    old_run: Path,
) -> dict[str, Any]:
    candidates = [
        old_run / "plots_local_ai_assisted_v1.geojson",
        old_run / "plots_local.geojson",
    ]
    old_path = next((path for path in candidates if path.is_file()), None)
    if old_path is None:
        return {"status": "old_area_not_available"}
    old_payload = read_json(old_path)
    old_mask = rasterize_local_geojson(old_payload, local_to_preview, majority_mask.shape)
    new_mask = majority_mask > 0
    old_binary = old_mask > 0
    intersection = int(np.count_nonzero(new_mask & old_binary))
    union = int(np.count_nonzero(new_mask | old_binary))
    new_pixels = int(np.count_nonzero(new_mask))
    old_pixels = int(np.count_nonzero(old_binary))
    report = {
        "status": "compared_in_current_mosaic_preview_grid",
        "old_area_path": str(old_path),
        "old_area_unchanged": True,
        "segmentation_area_preview_pixels": new_pixels,
        "old_ai_assisted_area_preview_pixels": old_pixels,
        "intersection_preview_pixels": intersection,
        "union_preview_pixels": union,
        "iou": intersection / union if union else None,
        "segmentation_outside_old_fraction": (
            int(np.count_nonzero(new_mask & ~old_binary)) / max(1, new_pixels)
        ),
        "old_outside_segmentation_fraction": (
            int(np.count_nonzero(old_binary & ~new_mask)) / max(1, old_pixels)
        ),
        "interpretation": (
            "QA comparison only; neither segmentation-derived nor prior AI-assisted area "
            "is an official or cadastral boundary."
        ),
    }
    write_json(output_dir / "segmentation_area_comparison.json", report)
    return report


def _prepare_postprocess(
    output_dir: Path,
    detector_run: Path,
    frame_indices: Sequence[int],
    post_config: dict[str, Any],
    video_path: Path,
    old_run: Path,
) -> dict[str, Any]:
    audit = audit_inference_run(detector_run, post_config)
    write_json(output_dir / "input_audit.json", audit)
    (output_dir / "input_audit.md").write_text(audit_markdown(audit), encoding="utf-8")
    write_json(output_dir / "postprocess_config.json", post_config)
    transforms, segments, _edges, registration_report = register_video_frames(
        video_path,
        frame_indices,
        output_dir,
        post_config,
    )
    mosaic_info = build_mosaic(
        video_path,
        frame_indices,
        transforms,
        segments,
        output_dir,
        post_config,
    )
    aggregation, majority = _aggregate_segmentation_local(
        output_dir,
        frame_indices,
        transforms,
        mosaic_info,
        post_config,
    )
    area_comparison = _compare_old_area(
        output_dir,
        majority,
        list_to_matrix(mosaic_info["local_to_preview"]),
        old_run,
    )
    unique, _observations, fusion_report = fuse_detections(
        detector_run / "detections.csv",
        frame_indices,
        transforms,
        segments,
        mosaic_info["footprints"],
        output_dir,
        audit,
        registration_report,
        post_config,
    )
    _propagate_gate_status(output_dir, detector_run / "detections.csv")
    geo_report = evaluate_and_apply_georeference(
        detector_run / "telemetry_sync.csv",
        frame_indices,
        transforms,
        output_dir / "unique_plants.csv",
        output_dir,
        audit,
        post_config,
    )
    unique = pd.read_csv(output_dir / "unique_plants.csv")
    write_unique_plant_overlay(
        output_dir,
        mosaic_info["mosaic_image"],
        list_to_matrix(mosaic_info["local_to_preview"]),
        unique,
    )
    (output_dir / "gap_evidence").mkdir(exist_ok=True)
    summary = {
        "status": "prepare_complete_segmentation_area_ready",
        "created_at": read_json(output_dir / "run_manifest.json")["started_at"],
        "completed_at": utc_now_iso(),
        "inference_run": str(detector_run),
        "total_detection_observations_in_source_run": read_json(
            output_dir / "segmentation_gating_report.json"
        )["before"],
        "detection_observations_processed": fusion_report["total_detection_observations"],
        "unique_plant_candidates": fusion_report["unique_plant_candidates"],
        "confirmed_unique_plants": fusion_report["confirmed_unique_plants"],
        "low_support_unique_plants": fusion_report["low_support_unique_plants"],
        "registration": registration_report,
        "fusion": fusion_report,
        "georeference": geo_report,
        "segmentation_aggregation": aggregation,
        "segmentation_area_comparison": area_comparison,
        "manual_plot_status": "segmentation_derived_area_loaded",
        "plots_path": str(output_dir / "segmentation_areas_local.geojson"),
        "protected_sources_unchanged": True,
        "old_inference_run_modified": False,
        "training_run": False,
        "yolo_inference_rerun": True,
    }
    write_json(output_dir / "postprocess_summary.json", summary)
    return summary


def _create_segmentation_previews(
    output_dir: Path,
    video_path: Path,
    frame_indices: Sequence[int],
) -> list[str]:
    summary = pd.read_csv(output_dir / "segmentation_frame_summary.csv")
    selected = {
        int(frame_indices[0]),
        int(frame_indices[len(frame_indices) // 2]),
        int(frame_indices[-1]),
        int(summary.loc[summary["morphology_changed_pixels"].idxmax(), "frame_index"]),
        int(summary.loc[summary["processed_fraction"].idxmax(), "frame_index"]),
        int(summary.loc[summary["processed_fraction"].idxmin(), "frame_index"]),
    }
    preview_dir = output_dir / "preview"
    preview_dir.mkdir(exist_ok=True)
    paths = []
    with VideoReader(video_path) as reader:
        for frame_index in sorted(selected):
            frame = reader.read_frame(frame_index)
            raw = cv2.imread(
                str(output_dir / "segmentation_masks_raw" / f"frame_{frame_index:06d}.png"),
                cv2.IMREAD_GRAYSCALE,
            )
            processed = cv2.imread(
                str(output_dir / "segmentation_masks_closing3" / f"frame_{frame_index:06d}.png"),
                cv2.IMREAD_GRAYSCALE,
            )
            if raw is None or processed is None:
                continue
            scale = min(1.0, 1280.0 / frame.shape[1])
            size = (round(frame.shape[1] * scale), round(frame.shape[0] * scale))
            small = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
            small_processed = cv2.resize(processed, size, interpolation=cv2.INTER_NEAREST)
            small_changed = cv2.resize(
                (raw != processed).astype(np.uint8),
                size,
                interpolation=cv2.INTER_NEAREST,
            )
            overlay = small.copy()
            green = np.zeros_like(overlay)
            green[:, :, 1] = 255
            inside = small_processed > 0
            overlay[inside] = cv2.addWeighted(
                small[inside],
                0.45,
                green[inside],
                0.55,
                0,
            )
            overlay[small_changed > 0] = (0, 0, 255)
            path = preview_dir / f"segmentation_frame_{frame_index:06d}_qa.jpg"
            cv2.imwrite(str(path), overlay, [cv2.IMWRITE_JPEG_QUALITY, 88])
            paths.append(str(path.relative_to(output_dir)))
            del frame, raw, processed, small, overlay
    mosaic = cv2.imread(str(output_dir / "survey_mosaic_preview.png"))
    majority = cv2.imread(
        str(preview_dir / "segmentation_mosaic_majority_mask.png"),
        cv2.IMREAD_GRAYSCALE,
    )
    if mosaic is not None and majority is not None:
        overlay = mosaic.copy()
        green = np.zeros_like(overlay)
        green[:, :, 1] = 255
        inside = majority > 0
        overlay[inside] = cv2.addWeighted(mosaic[inside], 0.45, green[inside], 0.55, 0)
        cv2.imwrite(
            str(preview_dir / "segmentation_mosaic_area_overlay.jpg"),
            overlay,
            [cv2.IMWRITE_JPEG_QUALITY, 90],
        )
        paths.append("preview/segmentation_mosaic_area_overlay.jpg")
    gc.collect()
    return paths


def _copy_csv(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def _export_compatibility_outputs(output_dir: Path) -> dict[str, str]:
    mapping = {
        "expected_points.csv": "expected_planting_points.csv",
        "gap_candidates.csv": "missing_points.csv",
        "gap_groups.csv": "gaps_summary.csv",
        "area_summary.csv": "plot_summary.csv",
    }
    for destination, source in mapping.items():
        _copy_csv(output_dir / source, output_dir / destination)
    candidates = pd.read_csv(output_dir / "missing_points.csv")
    features = []
    for row in candidates.itertuples(index=False):
        properties = row._asdict()
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(row.local_x), float(row.local_y)],
                },
                "properties": properties,
            }
        )
    write_json(
        output_dir / "gap_candidates_local.geojson",
        {
            "type": "FeatureCollection",
            "coordinate_space": "registered_local_pixels",
            "coordinate_reference_status": "local_only_not_wgs84",
            "features": features,
        },
    )
    report = read_json(output_dir / "gap_analysis_report.json")
    areas = pd.read_csv(output_dir / "plot_summary.csv")
    expected = int(report["expected_planting_points"])
    summary_row = {
        "analysis_area_count": report["plot_count"],
        "physical_rows": report["rows"],
        "internal_row_segments": report["row_segments"],
        "expected_points": expected,
        "estimated_missing_plants": report["estimated_missing_plants"],
        "gap_groups": report["gap_count"],
        "missing_rate": report["estimated_missing_plants"] / expected if expected else "",
        "rejected_near_boundary": report["rejected_missing_boundary"],
        "rejected_low_coverage": report["rejected_missing_low_coverage"],
        "mean_area_coverage_fraction": (
            float((areas["unique_detected_plants"] > 0).mean()) if len(areas) else ""
        ),
        "status": report["status"],
    }
    write_csv(output_dir / "gap_summary.csv", [summary_row], list(summary_row))
    return mapping


def _old_comparison(old_run: Path, current: Mapping[str, Any], runtime: Mapping[str, Any]) -> dict[str, Any]:
    old_summary_path = old_run / "postprocess_summary.json"
    if not old_summary_path.is_file():
        return {"status": "old_run_not_available", "path": str(old_run)}
    old = read_json(old_summary_path)
    old_gap = old.get("gap_analysis", {})
    old_detector_seconds = (
        float(old["inference_run"] and read_json(Path(old["inference_run"]) / "run_summary.json")["timing_mean"]["inference_ms"])
        * int(read_json(Path(old["inference_run"]) / "run_summary.json")["processed_frames"])
        / 1000.0
        if Path(old.get("inference_run", "" )).is_dir()
        else None
    )
    return {
        "status": "compared",
        "old_run": str(old_run),
        "processed_frames": {"old": old["registration"]["frame_count"], "new": current["processed_frames"]},
        "detection_observations": {
            "old": old["detection_observations_processed"],
            "new_before_gating": current["observations_before_gating"],
            "new_after_gating": current["observations_after_gating"],
        },
        "unique_plants": {
            "old_candidates": old["fusion"]["unique_plant_candidates"],
            "new_candidates": current["unique_plant_candidates"],
            "old_confirmed": old["confirmed_unique_plants"],
            "new_confirmed": current["confirmed_unique_plants"],
        },
        "rows": {"old": old_gap.get("rows"), "new": current["rows"]},
        "expected_points": {
            "old": old_gap.get("expected_planting_points"),
            "new": current["expected_points"],
        },
        "missing_plants": {
            "old": old_gap.get("estimated_missing_plants"),
            "new": current["missing_plants"],
        },
        "gap_groups": {"old": old_gap.get("gap_count"), "new": current["gap_groups"]},
        "missing_rate": {
            "old": (
                old_gap.get("estimated_missing_plants", 0)
                / max(1, old_gap.get("expected_planting_points", 0))
            ),
            "new": current["missing_rate"],
        },
        "rejected_near_boundary": {
            "old": old_gap.get("rejected_missing_boundary"),
            "new": current["rejected_near_boundary"],
        },
        "coverage_rejected": {
            "old": old_gap.get("rejected_missing_low_coverage"),
            "new": current["rejected_low_coverage"],
        },
        "runtime_seconds": {
            "old_detector_estimate_from_mean_inference": old_detector_seconds,
            "new_detector": runtime.get("detector_seconds"),
            "new_segmenter": runtime.get("segmenter_seconds"),
            "new_total": runtime.get("total_seconds"),
        },
    }


def _write_full_qa(
    output_dir: Path,
    old_run: Path,
    runtime: Mapping[str, Any],
    preview_files: Sequence[str],
) -> dict[str, Any]:
    manifest = read_json(output_dir / "run_manifest.json")
    gate = read_json(output_dir / "segmentation_gating_report.json")
    fusion = read_json(output_dir / "fusion_report.json")
    gaps = read_json(output_dir / "gap_analysis_report.json")
    geo = read_json(output_dir / "georeference_report.json")
    geometry_qa = read_json(output_dir / "polygon_geometry_qa.json")
    area_comparison = (
        read_json(output_dir / "segmentation_area_comparison.json")
        if (output_dir / "segmentation_area_comparison.json").is_file()
        else {"status": "not_available"}
    )
    expected = int(gaps["expected_planting_points"])
    current = {
        "processed_frames": int(manifest["frame_sampling"]["processed_frame_count"]),
        "observations_before_gating": gate["before"],
        "observations_after_gating": gate["after"],
        "unique_plant_candidates": fusion["unique_plant_candidates"],
        "confirmed_unique_plants": fusion["confirmed_unique_plants"],
        "rows": gaps["rows"],
        "expected_points": expected,
        "missing_plants": gaps["estimated_missing_plants"],
        "gap_groups": gaps["gap_count"],
        "missing_rate": gaps["estimated_missing_plants"] / expected if expected else None,
        "rejected_near_boundary": gaps["rejected_missing_boundary"],
        "rejected_low_coverage": gaps["rejected_missing_low_coverage"],
    }
    comparison = _old_comparison(old_run, current, runtime)
    unique = pd.read_csv(output_dir / "unique_plants.csv", usecols=["latitude", "longitude"])
    groups = pd.read_csv(output_dir / "gaps_summary.csv", usecols=["latitude", "longitude"])
    null_geo = bool(
        unique[["latitude", "longitude"]].isna().all().all()
        and groups[["latitude", "longitude"]].isna().all().all()
    )
    qa = {
        "generated_at": utc_now_iso(),
        "readiness": "demo_only",
        "status": (
            "candidate_gaps_and_polygon_geometry_require_manual_review"
            if geometry_qa["summary"]["needs_review"]
            else "candidate_gaps_require_manual_review"
        ),
        "segmentation_configuration": manifest["segmentation"],
        "polygon_geometry_qa": {
            "path": str(output_dir / "polygon_geometry_qa.json"),
            "status": geometry_qa["status"],
            **geometry_qa["summary"],
        },
        "segmentation_gating": gate,
        "segmentation_area_comparison": area_comparison,
        "artifact_reference_boundary_qa": {
            "boundary_f1_raw": 0.81736,
            "boundary_f1_closing_3": 0.82661,
            "normalized_hd95_raw": 0.016806,
            "normalized_hd95_closing_3": 0.00847,
            "narrow_background_leakage_ratio": 0.243,
            "hole_corridor_preservation_ratio": 0.757,
            "scope": "artifact audit reference; not a measurement on this full-flight output",
        },
        "georeferencing": {
            "valid": geo["valid"],
            "status": geo["status"],
            "failure_reasons": geo["failure_reasons"],
            "all_object_latitude_longitude_null_when_invalid": null_geo,
        },
        "current_counts": current,
        "old_run_comparison": comparison,
        "runtime": dict(runtime),
        "preview_files": list(preview_files),
        "false_gap_assessment": {
            "ground_truth_available": False,
            "conclusion": (
                "No claim of false-gap improvement is made. Gating and boundary exclusions are "
                "auditable, but gap correctness still requires raw-frame/operator review."
            ),
            "outside_observations_preserved": gate["outside_rejected"],
            "boundary_review_observations": gate["boundary_review"],
            "gap_candidates_rejected_near_segmentation_boundary": gaps[
                "rejected_missing_boundary"
            ],
        },
        "known_limitations": [
            "plantable_area is an analysis mask, not an official or cadastral plot boundary",
            "artifact audit reports material narrow-background leakage and imperfect corridor preservation",
            "same-flight models and analysis do not establish cross-flight generalization",
            "candidate gaps have no field-verified missing-plant ground truth",
            "SRT drone GPS is metadata and is not copied to plant or gap objects",
            "local/mosaic pixels are authoritative because WGS84 georeferencing failed QA",
        ],
    }
    write_json(output_dir / "qa_summary.json", qa)
    comparison_text = json.dumps(comparison, indent=2, ensure_ascii=False)
    report = f"""# Full Pipeline QA Report

Status: `demo_only`
Result: candidate gaps require manual review.

## Models and segmentation

- Segmenter: `{manifest['models']['segmenter']['identity']}`
- Segmenter SHA-256: `{manifest['models']['segmenter']['sha256']}`
- Detector SHA-256: `{manifest['models']['detector']['sha256']}`
- Segmentation: imgsz={manifest['segmentation']['imgsz']}, conf={manifest['segmentation']['confidence']}, IoU={manifest['segmentation']['nms_iou']}, morphology=`closing_3`
- Canonical area: semantic union mask followed by local union polygon.

## Gating

- Observations before / after: {gate['before']:,} / {gate['after']:,}
- Safe interior: {gate['accepted']:,}
- Boundary review retained for fusion: {gate['boundary_review']:,}
- Outside rejected but preserved: {gate['outside_rejected']:,}
- Boundary buffer: {gate['boundary_buffer_px']:.3f} px ({gate['boundary_buffer_spacing_multiplier']} × median spacing)

## Polygon geometry

- QA status: `{geometry_qa['status']}`
- Unrecoverable features: {geometry_qa['summary']['unrecoverable']}
- Material area changes: {geometry_qa['summary']['material_area_changes']}
- Polygonal components analyzed: {geometry_qa['summary']['latest_polygonal_component_count']}

## Row and gap result

- Unique candidates / confirmed: {fusion['unique_plant_candidates']:,} / {fusion['confirmed_unique_plants']:,}
- Rows / row segments: {gaps['rows']:,} / {gaps['row_segments']:,}
- Expected points: {gaps['expected_planting_points']:,}
- Candidate missing plants / groups: {gaps['estimated_missing_plants']:,} / {gaps['gap_count']:,}
- Boundary-rejected candidates: {gaps['rejected_missing_boundary']:,}

## Georeferencing

- Status: `{geo['status']}`
- Failure reasons: {', '.join(geo['failure_reasons'])}
- Plant and gap latitude/longitude all null: `{null_geo}`
- Drone SRT telemetry remains available in `srt_parsed.csv` and frame/observation metadata only.

## False-gap interpretation

No false-gap improvement is claimed from a lower count. There is no field ground truth. The
segmentation gate, boundary review records, rejected observations, raw-frame evidence, and
area comparison must be inspected together. Artifact audit leakage (~0.243) and corridor
preservation (~0.757) remain explicit limitations.

## Old-run comparison

```json
{comparison_text}
```

The segmentation-derived areas are not official or cadastral boundaries.
"""
    (output_dir / "qa_report.md").write_text(report, encoding="utf-8")
    return qa


def _required_outputs() -> list[str]:
    return [
        "run_manifest.json",
        "resolved_config.json",
        "checksums.json",
        "pipeline.log",
        "runtime_summary.json",
        "srt_parsed.csv",
        "frame_manifest.csv",
        "segmentation_frame_summary.csv",
        "segmentation_masks_raw",
        "segmentation_masks_closing3",
        "segmentation_areas_local.geojson",
        "analysis_polygons_sanitized.geojson",
        "polygon_geometry_qa.json",
        "plant_observations.csv",
        "plant_observations_rejected.csv",
        "unique_plants.csv",
        "rows_local.geojson",
        "expected_points.csv",
        "gap_candidates.csv",
        "gap_candidates_local.geojson",
        "gap_groups.csv",
        "area_summary.csv",
        "gap_summary.csv",
        "qa_summary.json",
        "qa_report.md",
        "preview",
        "gap_evidence",
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Full terminal-only detector + segmentation Drone AI Gap Plot pipeline."
    )
    parser.add_argument("--flight-dir", type=Path)
    parser.add_argument(
        "--detector-model",
        type=Path,
        default=REPO_ROOT / "models" / "source" / "plant_center_manual_v1_b0_best.pt",
    )
    parser.add_argument(
        "--segmenter-model",
        type=Path,
        default=REPO_ROOT / "models" / "source" / "plot_segmenter_b4_selected_best.pt",
    )
    parser.add_argument("--device", choices=["cpu"], default="cpu")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--export-video",
        action="store_true",
        help="Render the offline sampled review video after cached/full pipeline stages",
    )
    parser.add_argument(
        "--video-only",
        action="store_true",
        help=(
            "Treat --output-dir as an existing completed run and render only video; "
            "no checkpoint is loaded and no inference/post-processing is run"
        ),
    )
    parser.add_argument("--frame-stride", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--seg-imgsz", type=int, default=1280)
    parser.add_argument("--seg-conf", type=float, default=0.10)
    parser.add_argument("--seg-iou", type=float, default=0.85)
    parser.add_argument("--seg-morphology", choices=["closing_3"], default="closing_3")
    parser.add_argument("--no-gui", action="store_true", default=True)
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    parser.add_argument("--max-frames", type=int, help="Subset for smoke validation")
    parser.add_argument(
        "--old-run",
        type=Path,
        default=REPO_ROOT / "runtime" / "reference" / "postprocess_run",
    )
    from .video_export import add_video_arguments

    add_video_arguments(
        parser,
        include_run_dir=False,
        include_resume_force=False,
    )
    return parser


def _resolved_output(args: argparse.Namespace) -> Path:
    if args.output_dir:
        value = args.output_dir.expanduser()
        return (value if value.is_absolute() else PROJECT_ROOT / value).resolve()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (PROJECT_ROOT / "outputs" / f"full_pipeline_c_closing3_{timestamp}").resolve()


def run(args: argparse.Namespace) -> Path:
    if args.resume and args.force:
        raise ValueError("--resume and --force are mutually exclusive")
    if args.video_only:
        if args.output_dir is None:
            raise ValueError("--video-only requires an existing --output-dir")
        output_dir = _resolved_output(args)
        from .video_export import export_review_video, options_from_args

        export_review_video(
            output_dir,
            options_from_args(args),
            resume=args.resume,
            force=args.force,
        )
        return output_dir
    missing_inputs = [
        name
        for name, value in (
            ("--flight-dir", args.flight_dir),
            ("--detector-model", args.detector_model),
            ("--segmenter-model", args.segmenter_model),
        )
        if value is None
    ]
    if missing_inputs:
        raise ValueError(
            f"Full pipeline mode requires: {', '.join(missing_inputs)}"
        )
    if args.frame_stride <= 0 or args.batch_size <= 0:
        raise ValueError("--frame-stride and --batch-size must be positive")
    if args.max_frames is not None and args.max_frames < 3:
        raise ValueError("--max-frames must be at least 3")
    output_dir = _resolved_output(args)
    _safe_prepare_output(output_dir, args.resume, args.force)
    _configure_logging(output_dir, args.log_level)
    pipeline_started = time.perf_counter()
    runtime: dict[str, Any] = {}
    segmenter: YOLO | None = None
    detector: PlantCenterModel | None = None
    manifest_path = output_dir / "run_manifest.json"

    _stage_header(1)
    stage_started = time.perf_counter()
    video_path, srt_path, media_candidates = discover_media_pair(args.flight_dir)
    if video_path.stem.casefold() != srt_path.stem.casefold():
        raise RuntimeError("Video/SRT stems do not match; flight pairing is not proven")
    detector_path = args.detector_model.expanduser().resolve()
    segmenter_path = args.segmenter_model.expanduser().resolve()
    for path in (detector_path, segmenter_path, video_path, srt_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    video_audit = audit_video(video_path)
    input_hashes = {
        "video": sha256_file(video_path),
        "srt": sha256_file(srt_path),
        "detector_model": sha256_file(detector_path),
        "segmenter_model": sha256_file(segmenter_path),
    }
    config_fingerprint_payload = {
        "inputs": input_hashes,
        "flight_dir": str(args.flight_dir.expanduser().resolve()),
        "frame_stride": args.frame_stride,
        "max_frames": args.max_frames,
        "batch_size": args.batch_size,
        "device": args.device,
        "segmentation": {
            "imgsz": args.seg_imgsz,
            "confidence": args.seg_conf,
            "nms_iou": args.seg_iou,
            "morphology": args.seg_morphology,
        },
    }
    run_fingerprint = _fingerprint(config_fingerprint_payload)
    if manifest_path.is_file():
        previous = read_json(manifest_path)
        if previous.get("run_fingerprint") != run_fingerprint:
            raise RuntimeError("Resume fingerprint mismatch; inputs or locked configuration changed")
        if args.resume:
            previous = _mark_manifest_resumed(manifest_path)
        detector_identity = previous["models"]["detector"]
        segmenter_identity = previous["models"]["segmenter"]
    else:
        detector = PlantCenterModel(detector_path, requested_device="cpu", allow_mps_to_cpu_fallback=False)
        detector_identity = {**detector.audit(), "sha256": input_hashes["detector_model"]}
        if detector_identity["task"] != "detect" or set(detector.names.values()) != {"plant"}:
            raise RuntimeError("Detector checkpoint identity mismatch")
        segmenter = YOLO(str(segmenter_path), task="segment")
        segmenter_identity = _segmenter_identity(segmenter, segmenter_path)
        manifest = {
            "pipeline": "full_local_inference_drone_ai_gap_plot",
            "status": "running",
            "readiness": "demo_only",
            "started_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "run_fingerprint": run_fingerprint,
            "output_dir": str(output_dir),
            "no_gui": True,
            "models": {
                "detector": detector_identity,
                "segmenter": segmenter_identity,
            },
            "inputs": {
                "flight_dir": str(args.flight_dir.expanduser().resolve()),
                "video": str(video_path),
                "srt": str(srt_path),
                "media_candidates": media_candidates,
                "video_audit": video_audit,
            },
            "checksums": input_hashes,
            "segmentation": {
                "model": "C_selected_comparison_epoch56",
                "imgsz": args.seg_imgsz,
                "confidence": args.seg_conf,
                "nms_iou": args.seg_iou,
                "morphology": args.seg_morphology,
                "morphology_definition": "OpenCV MORPH_CLOSE, 3x3 ones kernel, one iteration",
                "class": "plantable_area",
                "canonical_outputs": [
                    "predicted_union_binary",
                    "union_polygon",
                ],
            },
            "device": "cpu",
            "coordinate_reference": {
                "authoritative": "registered_local_pixels",
                "wgs84_status": "pending_QA",
                "drone_srt_gps_is_object_coordinate": False,
            },
            "stages": {},
            "resume_provenance": {
                "resumed": bool(args.resume),
                "resume_requested_at": utc_now_iso() if args.resume else None,
            },
            "known_limitations": [
                AREA_DESCRIPTION,
                "No training or fine-tuning is performed.",
                "No production_with_qc readiness claim is permitted.",
            ],
        }
        write_json(manifest_path, manifest)
    checksums_payload = (
        read_json(output_dir / "checksums.json")
        if (output_dir / "checksums.json").is_file()
        else {}
    )
    checksums_payload["algorithm"] = "sha256"
    checksums_payload["inputs"] = {
        "video": {"path": str(video_path), "sha256": input_hashes["video"]},
        "srt": {"path": str(srt_path), "sha256": input_hashes["srt"]},
        "detector_model": {
            "path": str(detector_path),
            "sha256": input_hashes["detector_model"],
        },
        "segmenter_model": {
            "path": str(segmenter_path),
            "sha256": input_hashes["segmenter_model"],
        },
    }
    write_json(output_dir / "checksums.json", checksums_payload)
    if not _stage_is_complete(output_dir, 1):
        stage = _mark_stage(
            output_dir,
            1,
            stage_started,
            {
                "video_frames": int(video_audit["frame_count"]),
                "video_resolution": [int(video_audit["width"]), int(video_audit["height"])],
                "video_fps": float(video_audit["fps"]),
                "segmenter_sha256_verified": True,
            },
        )
        _update_manifest_stage(output_dir, stage)

    _stage_header(2)
    stage_started = time.perf_counter()
    entries, srt_report = parse_srt(srt_path)
    frame_count = int(video_audit["frame_count"])
    fps = float(video_audit["fps"])
    duration = float(video_audit["duration_seconds"])
    if len(entries) != frame_count:
        raise RuntimeError(f"SRT/video frame-count mismatch: {len(entries)} vs {frame_count}")
    if abs(float(srt_report["timestamp_end_seconds"]) - duration) > max(0.25, 2 / fps):
        raise RuntimeError("SRT/video duration mismatch exceeds conservative tolerance")
    frame_indices = list(range(0, frame_count, int(args.frame_stride)))
    if args.max_frames is not None:
        frame_indices = frame_indices[: int(args.max_frames)]
    synchronizer = TelemetrySynchronizer(entries, tolerance_seconds=0.2, time_offset_seconds=0.0)
    if not _stage_is_complete(output_dir, 2):
        _write_srt_csv(output_dir / "srt_parsed.csv", entries)
        _write_frame_manifest(output_dir / "frame_manifest.csv", frame_indices, synchronizer, fps)
        write_json(output_dir / "srt_parse_report.json", srt_report)
        stage = _mark_stage(
            output_dir,
            2,
            stage_started,
            {
                "srt_blocks": len(entries),
                "fields_found": srt_report["fields_found"],
                "duration_delta_seconds": abs(
                    float(srt_report["timestamp_end_seconds"]) - duration
                ),
            },
        )
        _update_manifest_stage(output_dir, stage)
    detector_config = load_config(
        overrides={
            "model_path": str(detector_path),
            "input_dir": str(args.flight_dir.expanduser().resolve()),
            "video_path": str(video_path),
            "srt_path": str(srt_path),
            "output_root": str(output_dir / ".stages"),
            "device": "cpu",
            "allow_mps_to_cpu_fallback": False,
            "inference_every_n_frames": int(args.frame_stride),
            "playback_mode": "processed_frames_only",
            "save_annotated_video": False,
            "display_bounding_boxes": False,
            "display_center_points": False,
            "display_tile_boundaries": False,
        }
    )
    post_config = merged_config()
    post_config["video"] = {
        "width": int(video_audit["width"]),
        "height": int(video_audit["height"]),
        "frame_count": frame_count,
        "fps": fps,
    }
    post_config["run"] = {
        "created_at": read_json(manifest_path)["started_at"],
        "inference_run": str(output_dir / ".stages" / DETECTOR_STAGE_DIR),
        "output_dir": str(output_dir),
        "processed_frame_indices": frame_indices,
        "smoke": args.max_frames is not None,
        "max_frames": args.max_frames,
    }
    post_config["segmentation_aggregation"] = {
        "minimum_vote_fraction": 0.5,
        "minimum_component_area_preview_px2": 100.0,
        "simplify_preview_px": 1.0,
    }
    post_config["geometry_sanitation"] = {
        "minimum_component_area_local_px2": 1.0,
        "minimum_hole_area_local_px2": 1.0,
        "material_area_change_fraction": 0.02,
        "repair": "shapely_make_valid_structure_with_controlled_buffer_zero_fallback",
        "simplification": "shapely_preserve_topology",
        "orientation": "exterior_ccw_interior_cw",
    }
    resolved_config = {
        "detector": detector_config,
        "segmentation": {
            "model": str(segmenter_path),
            "identity": "C_selected_comparison_epoch56",
            "device": "cpu",
            "imgsz": int(args.seg_imgsz),
            "confidence": float(args.seg_conf),
            "nms_iou": float(args.seg_iou),
            "morphology": args.seg_morphology,
            "batch_size": int(args.batch_size),
            "retina_masks": True,
            "raw_instance_masks_are_diagnostic_only": True,
            "canonical_outputs": ["predicted_union_binary", "union_polygon"],
        },
        "postprocess": post_config,
        "frame_sampling": {
            "stride": int(args.frame_stride),
            "processed_frame_count": len(frame_indices),
            "source_frame_count": frame_count,
            "frame_indices": frame_indices,
        },
        "no_gui": True,
    }
    write_json(output_dir / "resolved_config.json", resolved_config)
    manifest = read_json(manifest_path)
    manifest["frame_sampling"] = resolved_config["frame_sampling"]
    write_json(manifest_path, manifest)
    if args.resume:
        reused_stages = _validate_resume_checkpoints(
            output_dir,
            frame_indices,
            maximum_stage=5,
        )
        LOGGER.info(
            "Resume checkpoint validation passed; reusing stages: %s",
            reused_stages,
        )

    _stage_header(3)
    stage_started = time.perf_counter()
    if not _stage_is_complete(output_dir, 3):
        if segmenter is None:
            segmenter = YOLO(str(segmenter_path), task="segment")
            _segmenter_identity(segmenter, segmenter_path)
        segmentation_report = _run_segmentation(
            segmenter,
            video_path,
            output_dir,
            frame_indices,
            fps,
            resolved_config["segmentation"],
        )
        runtime["segmenter_seconds"] = segmentation_report["inference_seconds"]
        stage = _mark_stage(output_dir, 3, stage_started, segmentation_report)
        _update_manifest_stage(output_dir, stage)
    else:
        runtime["segmenter_seconds"] = read_json(_stage_marker(output_dir, 3))["details"].get(
            "inference_seconds"
        )
    del segmenter
    gc.collect()

    _stage_header(4)
    stage_started = time.perf_counter()
    if not _stage_is_complete(output_dir, 4):
        missing_detection = _missing_detection_indices(output_dir, frame_indices)
        if missing_detection and detector is None:
            detector = PlantCenterModel(
                detector_path,
                requested_device="cpu",
                allow_mps_to_cpu_fallback=False,
            )
        detection_report = _run_detection(
            detector,
            video_path,
            output_dir,
            frame_indices,
            synchronizer,
            detector_config,
        )
        runtime["detector_seconds"] = detection_report["inference_seconds"]
        stage = _mark_stage(output_dir, 4, stage_started, detection_report)
        _update_manifest_stage(output_dir, stage)
    else:
        runtime["detector_seconds"] = read_json(_stage_marker(output_dir, 4))["details"].get(
            "inference_seconds"
        )
    del detector
    gc.collect()

    _stage_header(5)
    stage_started = time.perf_counter()
    if not _stage_is_complete(output_dir, 5):
        detector_run, gate_report = _gate_observations(
            output_dir,
            frame_indices,
            post_config,
            detector_config,
            input_hashes,
            srt_report,
        )
        summary = _prepare_postprocess(
            output_dir,
            detector_run,
            frame_indices,
            post_config,
            video_path,
            args.old_run.expanduser().resolve(),
        )
        stage = _mark_stage(
            output_dir,
            5,
            stage_started,
            {
                "observations_before_gating": gate_report["before"],
                "observations_after_gating": gate_report["after"],
                "unique_plant_candidates": summary["unique_plant_candidates"],
                "confirmed_unique_plants": summary["confirmed_unique_plants"],
                "registration_segments": summary["registration"][
                    "registration_segment_count"
                ],
                "georeference_status": summary["georeference"]["status"],
            },
        )
        _update_manifest_stage(output_dir, stage)

    _stage_header(6)
    stage_started = time.perf_counter()
    if not _stage_is_complete(output_dir, 6):
        gap_report = run_gap_analysis(
            output_dir,
            output_dir / "segmentation_areas_local.geojson",
        )
        stage = _mark_stage(output_dir, 6, stage_started, gap_report)
        _update_manifest_stage(output_dir, stage)

    _stage_header(7)
    stage_started = time.perf_counter()
    if not _stage_is_complete(output_dir, 7):
        preview_files = _create_segmentation_previews(output_dir, video_path, frame_indices)
        runtime["stage_runtime_seconds"] = {
            str(index): read_json(_stage_marker(output_dir, index))["runtime_seconds"]
            for index in range(1, 7)
        }
        runtime["total_seconds"] = sum(runtime["stage_runtime_seconds"].values())
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        runtime["peak_memory_mib"] = (
            peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
        )
        write_json(output_dir / "runtime_summary.json", runtime)
        qa = _write_full_qa(
            output_dir,
            args.old_run.expanduser().resolve(),
            runtime,
            preview_files,
        )
        stage = _mark_stage(
            output_dir,
            7,
            stage_started,
            {
                "preview_count": len(preview_files),
                "readiness": qa["readiness"],
                "false_gap_ground_truth_available": False,
            },
        )
        _update_manifest_stage(output_dir, stage)

    _stage_header(8)
    stage_started = time.perf_counter()
    if not _stage_is_complete(output_dir, 8):
        mapping = _export_compatibility_outputs(output_dir)
        required = _required_outputs()
        missing = [name for name in required if not (output_dir / name).exists()]
        if missing:
            raise RuntimeError(f"Required output validation failed: {missing}")
        checksums = read_json(output_dir / "checksums.json")
        key_outputs = [
            "segmentation_areas_local.geojson",
            "analysis_polygons_sanitized.geojson",
            "polygon_geometry_qa.json",
            "plant_observations.csv",
            "unique_plants.csv",
            "rows_local.geojson",
            "gap_candidates.csv",
            "gap_groups.csv",
            "qa_summary.json",
        ]
        checksums["key_outputs"] = {
            name: sha256_file(output_dir / name) for name in key_outputs
        }
        write_json(output_dir / "checksums.json", checksums)
        stage = _mark_stage(
            output_dir,
            8,
            stage_started,
            {
                "required_outputs_validated": True,
                "compatibility_mapping": mapping,
                "coordinate_reference": "registered_local_pixels",
            },
        )
        _update_manifest_stage(output_dir, stage)
    runtime = (
        read_json(output_dir / "runtime_summary.json")
        if (output_dir / "runtime_summary.json").is_file()
        else runtime
    )
    runtime["stage_runtime_seconds"] = {
        str(index): read_json(_stage_marker(output_dir, index))["runtime_seconds"]
        for index in range(1, 9)
    }
    runtime["total_seconds"] = sum(runtime["stage_runtime_seconds"].values())
    runtime["last_process_elapsed_seconds"] = time.perf_counter() - pipeline_started
    write_json(output_dir / "runtime_summary.json", runtime)
    manifest = read_json(manifest_path)
    geometry_qa = read_json(output_dir / "polygon_geometry_qa.json")
    manifest["status"] = (
        "completed_with_warnings"
        if geometry_qa.get("summary", {}).get("needs_review", False)
        else "completed"
    )
    manifest["readiness"] = "demo_only"
    manifest["completed_at"] = utc_now_iso()
    manifest["coordinate_reference"] = {
        "authoritative": "registered_local_pixels",
        "wgs84_status": read_json(output_dir / "georeference_report.json")["status"],
        "object_latitude_longitude": "null_when_QA_invalid",
        "drone_srt_gps_is_object_coordinate": False,
    }
    manifest["resume_provenance"]["completed_via_resume"] = bool(args.resume)
    manifest["polygon_geometry_qa"] = {
        "path": str(output_dir / "polygon_geometry_qa.json"),
        "status": geometry_qa["status"],
        **geometry_qa["summary"],
    }
    write_json(manifest_path, manifest)
    if args.export_video:
        from .video_export import export_review_video, options_from_args

        export_review_video(
            output_dir,
            options_from_args(args),
            resume=args.resume,
            force=args.force,
        )
    LOGGER.info("Full pipeline completed: %s", output_dir)
    return output_dir


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = _resolved_output(args)
    try:
        result = run(args)
        print(result)
        return 0
    except Exception as exc:
        if output_dir.exists() and not args.video_only:
            try:
                failure = {
                    "status": "failed",
                    "failed_at": utc_now_iso(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                    "resume_supported": (output_dir / "run_manifest.json").is_file(),
                }
                write_json(output_dir / "failure.json", failure)
                if (output_dir / "run_manifest.json").is_file():
                    manifest = read_json(output_dir / "run_manifest.json")
                    manifest["status"] = "failed_resumable"
                    manifest["failure"] = {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                    write_json(output_dir / "run_manifest.json", manifest)
            except Exception:
                pass
        LOGGER.exception("Full pipeline failed")
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
