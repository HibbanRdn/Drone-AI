from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional, Union

import cv2
import numpy as np

from .schema import FrameResult, Telemetry, json_safe


class RotatingJsonlWriter:
    def __init__(self, path: Path, max_bytes: int, backup_count: int):
        self.path = path
        self.max_bytes = max(1024, int(max_bytes))
        self.backup_count = max(1, int(backup_count))
        self.rotation_count = 0
        self.discarded_files = 0
        self._file = path.open("a", encoding="utf-8")

    def _rotate(self) -> None:
        self._file.flush()
        self._file.close()
        oldest = self.path.with_name(f"{self.path.stem}.{self.backup_count}.jsonl")
        if oldest.exists():
            oldest.unlink()
            self.discarded_files += 1
        for index in range(self.backup_count - 1, 0, -1):
            source = self.path.with_name(f"{self.path.stem}.{index}.jsonl")
            target = self.path.with_name(f"{self.path.stem}.{index + 1}.jsonl")
            if source.exists():
                os.replace(source, target)
        if self.path.exists():
            os.replace(
                self.path, self.path.with_name(f"{self.path.stem}.1.jsonl")
            )
        self._file = self.path.open("a", encoding="utf-8")
        self.rotation_count += 1

    def write(self, value: Dict[str, Any]) -> int:
        payload = (
            json.dumps(json_safe(value), ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )
        payload_bytes = len(payload.encode("utf-8"))
        self._file.flush()
        current_size = self.path.stat().st_size if self.path.exists() else 0
        if current_size > 0 and current_size + payload_bytes > self.max_bytes:
            self._rotate()
        self._file.write(payload)
        self._file.flush()
        return payload_bytes

    def close(self) -> None:
        self._file.flush()
        self._file.close()

    def disk_bytes(self) -> int:
        return sum(
            candidate.stat().st_size
            for candidate in self.path.parent.glob(f"{self.path.stem}*.jsonl")
            if candidate.is_file()
        )


class SessionWriter:
    def __init__(
        self,
        runtime_root: Union[str, Path],
        storage_config: Dict[str, Any],
        session_metadata: Dict[str, Any],
    ):
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.sessions_root = self.runtime_root / "sessions"
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        minimum_free_bytes = int(
            storage_config.get("min_free_bytes", 128 * 1024 * 1024)
        )
        available_bytes = shutil.disk_usage(self.runtime_root).free
        if available_bytes < minimum_free_bytes:
            raise RuntimeError(
                "ruang disk runtime tidak cukup: tersedia {} MiB, minimum {} MiB".format(
                    available_bytes // (1024 * 1024),
                    minimum_free_bytes // (1024 * 1024),
                )
            )
        base = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        session_dir = self.sessions_root / base
        counter = 1
        while session_dir.exists():
            session_dir = self.sessions_root / f"{base}_{counter:02d}"
            counter += 1
        session_dir.mkdir(parents=False)
        self.session_id = session_dir.name
        self.session_dir = session_dir
        self.snapshots_dir = session_dir / "snapshots"
        self.frames_dir = session_dir / "frames"
        self.overlays_dir = session_dir / "overlays"
        self.snapshots_dir.mkdir()
        self.frames_dir.mkdir()
        self.overlays_dir.mkdir()
        self._lock = threading.Lock()
        self._closed = False
        self._record_count = 0
        self._snapshot_count = 0
        self._warning_count = 0
        self._bytes_written = 0
        self._max_session_bytes = int(storage_config["max_session_bytes"])
        self._snapshot_bytes = 0
        self._save_sample_frames = bool(storage_config.get("save_sample_frames", False))
        self._sample_interval_seconds = float(
            storage_config.get("sample_interval_seconds", 5)
        )
        self._last_sample_monotonic = float("-inf")
        jsonl_backup_count = int(storage_config.get("jsonl_backup_count", 2))
        log_storage_reserve = int(storage_config["log_rotate_bytes"]) * (
            int(storage_config["log_backup_count"]) + 1
        )
        available_after_logs = max(
            3 * 1024 * (jsonl_backup_count + 1),
            self._max_session_bytes - log_storage_reserve,
        )
        jsonl_budget = int(available_after_logs * 0.75)
        per_jsonl_bytes = min(
            int(storage_config.get("jsonl_rotate_bytes", 80 * 1024 * 1024)),
            max(
                1024,
                jsonl_budget // (3 * (jsonl_backup_count + 1)),
            ),
        )
        self._snapshot_budget = max(
            0,
            self._max_session_bytes
            - log_storage_reserve
            - per_jsonl_bytes * 3 * (jsonl_backup_count + 1),
        )
        self._detections = RotatingJsonlWriter(
            session_dir / "detections.jsonl",
            per_jsonl_bytes,
            jsonl_backup_count,
        )
        self._telemetry = RotatingJsonlWriter(
            session_dir / "telemetry.jsonl",
            per_jsonl_bytes,
            jsonl_backup_count,
        )
        self._metrics = RotatingJsonlWriter(
            session_dir / "metrics.jsonl",
            per_jsonl_bytes,
            jsonl_backup_count,
        )
        self.logger = logging.getLogger(f"gap_plot_ai.{self.session_id}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.logger.handlers.clear()
        handler = RotatingFileHandler(
            session_dir / "app.log",
            maxBytes=int(storage_config["log_rotate_bytes"]),
            backupCount=int(storage_config["log_backup_count"]),
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
        )
        handler.formatter.converter = __import__("time").gmtime
        self.logger.addHandler(handler)
        error_handler = RotatingFileHandler(
            session_dir / "errors.log",
            maxBytes=int(storage_config["log_rotate_bytes"]),
            backupCount=int(storage_config["log_backup_count"]),
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(handler.formatter)
        self.logger.addHandler(error_handler)
        metadata = {
            **session_metadata,
            "schema_version": str(session_metadata.get("schema_version", "1.0")),
            "session_id": self.session_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "storage_policy": {
                "max_session_bytes": self._max_session_bytes,
                "min_free_bytes": minimum_free_bytes,
                "free_bytes_at_start": available_bytes,
                "jsonl_rotate_bytes": per_jsonl_bytes,
                "jsonl_backup_count": jsonl_backup_count,
                "snapshot_budget_bytes": self._snapshot_budget,
                "full_video_saved": False,
                "automatic_session_deletion": False,
            },
        }
        self._write_json_atomic(self.session_dir / "session.json", metadata)
        self.logger.info("session_started id=%s", self.session_id)

    @staticmethod
    def _write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(
            json.dumps(json_safe(value), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, path)

    def append(self, result: FrameResult, telemetry: Telemetry) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("session writer sudah ditutup")
            record = result.to_dict()
            record["schema_version"] = result.schema_version
            self._bytes_written += self._detections.write(record)
            self._bytes_written += self._telemetry.write(
                {
                    "schema_version": result.schema_version,
                    "session_id": self.session_id,
                    "frame_index": result.frame_index,
                    "capture_timestamp": result.capture_timestamp,
                    **telemetry.to_dict(),
                },
            )
            self._bytes_written += self._metrics.write(
                {
                    "schema_version": result.schema_version,
                    "session_id": self.session_id,
                    "frame_index": result.frame_index,
                    "capture_timestamp": result.capture_timestamp,
                    **record["metrics"],
                },
            )
            self._record_count += 1
            self._warning_count += len(result.warning)

    def save_sample(
        self, frame_bgr: np.ndarray, overlay_bgr: np.ndarray, frame_index: int
    ) -> bool:
        if not self._save_sample_frames:
            return False
        now = time.monotonic()
        if now - self._last_sample_monotonic < self._sample_interval_seconds:
            return False
        with self._lock:
            if self._closed or self._snapshot_bytes >= self._snapshot_budget:
                return False
            encoded_items = []
            for directory, image in (
                (self.frames_dir, frame_bgr),
                (self.overlays_dir, overlay_bgr),
            ):
                ok, encoded = cv2.imencode(
                    ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90]
                )
                if not ok:
                    self.logger.error("sample_encode_failed frame=%d", frame_index)
                    return False
                encoded_items.append((directory, encoded))
            total_bytes = sum(int(encoded.nbytes) for _, encoded in encoded_items)
            if self._snapshot_bytes + total_bytes > self._snapshot_budget:
                self.logger.warning("sample_skipped storage_cap")
                return False
            for directory, encoded in encoded_items:
                path = directory / f"frame_{frame_index:010d}.jpg"
                temp = path.with_suffix(".jpg.tmp")
                temp.write_bytes(encoded.tobytes())
                os.replace(temp, path)
            self._snapshot_bytes += total_bytes
            self._bytes_written += total_bytes
            self._last_sample_monotonic = now
            return True

    def save_snapshot(
        self, frame_bgr: np.ndarray, frame_index: int
    ) -> Optional[Path]:
        with self._lock:
            if self._closed or self._snapshot_bytes >= self._snapshot_budget:
                self.logger.warning("snapshot_skipped storage_cap_or_closed")
                return None
            path = self.snapshots_dir / f"frame_{frame_index:010d}.jpg"
            ok, encoded = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if not ok:
                self.logger.error("snapshot_encode_failed frame=%d", frame_index)
                return None
            if self._snapshot_bytes + int(encoded.nbytes) > self._snapshot_budget:
                self.logger.warning("snapshot_skipped storage_cap")
                return None
            temp = path.with_suffix(".jpg.tmp")
            temp.write_bytes(encoded.tobytes())
            os.replace(temp, path)
            self._bytes_written += int(encoded.nbytes)
            self._snapshot_bytes += int(encoded.nbytes)
            self._snapshot_count += 1
            return path

    def close(
        self, status: str = "stopped", summary_extra: Optional[Dict[str, Any]] = None
    ) -> Path:
        with self._lock:
            if self._closed:
                return self.session_dir / "session_summary.json"
            self._closed = True
            jsonl_writers = (self._detections, self._telemetry, self._metrics)
            for file in jsonl_writers:
                file.close()
            jsonl_rotations = sum(file.rotation_count for file in jsonl_writers)
            jsonl_files_discarded = sum(
                file.discarded_files for file in jsonl_writers
            )
            current_jsonl_bytes = sum(file.disk_bytes() for file in jsonl_writers)
            summary = {
                "schema_version": "1.0",
                "session_id": self.session_id,
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "inference_records": self._record_count,
                "snapshots": self._snapshot_count,
                "warnings": self._warning_count,
                "approx_bytes_written": self._bytes_written,
                "current_jsonl_bytes": current_jsonl_bytes,
                "jsonl_rotations": jsonl_rotations,
                "jsonl_files_discarded": jsonl_files_discarded,
                "snapshot_budget_bytes": self._snapshot_budget,
                "storage_cap_reached": (
                    self._snapshot_bytes >= self._snapshot_budget
                    or jsonl_files_discarded > 0
                ),
                **(summary_extra or {}),
            }
            summary_path = self.session_dir / "session_summary.json"
            self._write_json_atomic(summary_path, summary)
            self.logger.info("session_closed status=%s records=%d", status, self._record_count)
            for handler in list(self.logger.handlers):
                handler.flush()
                handler.close()
                self.logger.removeHandler(handler)
            return summary_path
