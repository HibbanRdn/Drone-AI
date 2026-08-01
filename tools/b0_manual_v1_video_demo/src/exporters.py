from __future__ import annotations

import csv
import json
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

import cv2
import numpy as np

from .detection_merge import MergeDiagnostics
from .runtime_diagnostics import environment_report, ffmpeg_codec_report
from .telemetry_sync import SyncResult
from .tiled_inference import Detection


DETECTION_COLUMNS = [
    "frame_index",
    "video_time_seconds",
    "detection_id",
    "class_id",
    "class_name",
    "confidence",
    "x1",
    "y1",
    "x2",
    "y2",
    "center_x",
    "center_y",
    "source_tile_x",
    "source_tile_y",
    "source_tile_width",
    "source_tile_height",
    "latitude",
    "longitude",
    "altitude",
    "gimbal_pitch",
    "srt_sync_status",
]
FRAME_SUMMARY_COLUMNS = [
    "frame_index",
    "video_time_seconds",
    "plant_count",
    "raw_tile_predictions",
    "after_global_nms",
    "after_center_suppression",
    "tile_count",
    "inference_ms",
    "render_ms",
    "effective_fps",
    "srt_index",
    "sync_delta_seconds",
    "sync_status",
]
TELEMETRY_COLUMNS = [
    "frame_index",
    "video_time_seconds",
    "srt_index",
    "srt_start_seconds",
    "srt_end_seconds",
    "sync_delta_seconds",
    "sync_status",
    "latitude",
    "longitude",
    "relative_altitude",
    "absolute_altitude",
    "gimbal_yaw",
    "gimbal_pitch",
    "gimbal_roll",
    "raw_text",
]


def _ensure_child(root: Path, destination: Path) -> None:
    resolved_root = root.resolve()
    resolved_destination = destination.resolve()
    if resolved_destination != resolved_root and resolved_root not in resolved_destination.parents:
        raise ValueError(f"Output harus berada di bawah {resolved_root}: {resolved_destination}")


class AnnotatedVideoWriter:
    def __init__(self, run_dir: Path, width: int, height: int, fps: float, log: TextIO):
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps)
        self.frame_count = 0
        self.backend = "none"
        self.path = run_dir / "annotated_video.mp4"
        self.process: subprocess.Popen[bytes] | None = None
        self.opencv_writer: cv2.VideoWriter | None = None
        self.log = log
        codec = ffmpeg_codec_report()
        if codec.get("ffmpeg_available") and codec.get("libx264_available"):
            command = [
                str(codec["ffmpeg_path"]),
                "-y",
                "-loglevel",
                "warning",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "-s",
                f"{self.width}x{self.height}",
                "-r",
                f"{self.fps:.12f}",
                "-i",
                "-",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(self.path),
            ]
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=log)
            self.backend = "ffmpeg_libx264"
        else:
            writer = cv2.VideoWriter(
                str(self.path), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (self.width, self.height)
            )
            if writer.isOpened():
                self.opencv_writer = writer
                self.backend = "opencv_mp4v"
            else:
                writer.release()
                self.path = run_dir / "annotated_video.avi"
                writer = cv2.VideoWriter(
                    str(self.path), cv2.VideoWriter_fourcc(*"MJPG"), self.fps, (self.width, self.height)
                )
                if not writer.isOpened():
                    writer.release()
                    raise OSError("FFmpeg H.264, OpenCV mp4v, dan AVI fallback semuanya gagal.")
                self.opencv_writer = writer
                self.backend = "opencv_mjpg_avi"

    def write(self, frame: np.ndarray) -> None:
        if frame.shape[:2] != (self.height, self.width):
            raise ValueError(
                f"Resolusi output berubah: {frame.shape[1]}x{frame.shape[0]}, "
                f"diharapkan {self.width}x{self.height}."
            )
        if self.process:
            if not self.process.stdin:
                raise BrokenPipeError("stdin FFmpeg tidak tersedia.")
            self.process.stdin.write(frame.tobytes())
        elif self.opencv_writer:
            self.opencv_writer.write(frame)
        else:
            raise RuntimeError("Writer belum dibuka.")
        self.frame_count += 1

    def close(self) -> None:
        if self.process:
            if self.process.stdin:
                self.process.stdin.close()
            return_code = self.process.wait(timeout=120)
            if return_code != 0:
                raise OSError(f"FFmpeg berhenti dengan status {return_code}.")
            self.process = None
        if self.opencv_writer:
            self.opencv_writer.release()
            self.opencv_writer = None

    def validate(self) -> dict[str, object]:
        if not self.path.is_file() or self.path.stat().st_size <= 0:
            raise OSError(f"Output video kosong atau tidak ditemukan: {self.path}")
        capture = cv2.VideoCapture(str(self.path))
        opened = capture.isOpened()
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        ok, _ = capture.read() if opened else (False, None)
        capture.release()
        duration = frame_count / fps if fps > 0 else 0.0
        passed = (
            opened
            and ok
            and width == self.width
            and height == self.height
            and frame_count > 0
            and self.path.stat().st_size > 0
        )
        if not passed:
            raise OSError("Validasi reopen output video gagal.")
        return {
            "path": str(self.path),
            "backend": self.backend,
            "opened": opened,
            "first_frame_readable": ok,
            "width": width,
            "height": height,
            "fps": fps,
            "frame_count": frame_count,
            "expected_written_frames": self.frame_count,
            "duration_seconds": duration,
            "file_size_bytes": self.path.stat().st_size,
            "audio_preserved": False,
            "passed": passed,
        }


class RunExporter:
    def __init__(
        self,
        output_root: str | Path,
        config: dict[str, Any],
        width: int,
        height: int,
        output_fps: float,
        run_name: str | None = None,
    ):
        self.output_root = Path(output_root).expanduser().resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        name = run_name or datetime.now().strftime("run_%Y%m%d_%H%M%S")
        self.run_dir = self.output_root / name
        if run_name is None:
            suffix = 1
            while self.run_dir.exists():
                self.run_dir = self.output_root / f"{name}_{suffix:02d}"
                suffix += 1
        _ensure_child(self.output_root, self.run_dir)
        self.run_dir.mkdir(parents=False, exist_ok=False)
        self.snapshots_dir = self.run_dir / "snapshots"
        self.snapshots_dir.mkdir()
        self.runtime_log = (self.run_dir / "runtime.log").open("w", encoding="utf-8")
        self.runtime_log.write(
            f"{datetime.now().isoformat()} run_started output={self.run_dir}\n"
        )
        self.runtime_log.flush()
        self._configure_logging()
        self.files: list[TextIO] = []
        self.detection_writer = self._csv_writer("detections.csv", DETECTION_COLUMNS)
        self.frame_writer = self._csv_writer("frame_summary.csv", FRAME_SUMMARY_COLUMNS)
        self.telemetry_writer = self._csv_writer("telemetry_sync.csv", TELEMETRY_COLUMNS)
        self.jsonl = (self.run_dir / "detections.jsonl").open("w", encoding="utf-8")
        self.files.append(self.jsonl)
        self.video_writer = (
            AnnotatedVideoWriter(self.run_dir, width, height, output_fps, self.runtime_log)
            if config.get("save_annotated_video", True)
            else None
        )
        self.processed_frames = 0
        self.total_detections = 0
        self.run_summary: dict[str, Any] = {}
        self._write_json(config, "run_config.json")
        self._write_json(environment_report(), "environment.json")

    def _configure_logging(self) -> None:
        self.log_handler = logging.StreamHandler(self.runtime_log)
        self.log_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        self.run_logger = logging.getLogger("b0_video_demo")
        self.run_logger.setLevel(logging.INFO)
        self.run_logger.addHandler(self.log_handler)

    def _csv_writer(self, name: str, columns: list[str]) -> csv.DictWriter:
        handle = (self.run_dir / name).open("w", encoding="utf-8", newline="")
        self.files.append(handle)
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        return writer

    def _write_json(self, data: object, name: str) -> None:
        with (self.run_dir / name).open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")

    def write_processed_frame(
        self,
        frame_index: int,
        sync: SyncResult,
        detections: list[Detection],
        diagnostics: MergeDiagnostics,
        tile_count: int,
        inference_ms: float,
        render_ms: float,
        annotated_frame: np.ndarray,
    ) -> None:
        telemetry = sync.telemetry
        altitude = telemetry.altitude if telemetry else None
        for detection_id, detection in enumerate(detections):
            row = {
                "frame_index": frame_index,
                "video_time_seconds": sync.video_time_seconds,
                "detection_id": detection_id,
                **detection.to_dict(),
                "latitude": telemetry.latitude if telemetry else None,
                "longitude": telemetry.longitude if telemetry else None,
                "altitude": altitude,
                "gimbal_pitch": telemetry.gimbal_pitch if telemetry else None,
                "srt_sync_status": sync.sync_status,
            }
            self.detection_writer.writerow({column: row.get(column) for column in DETECTION_COLUMNS})
        effective_fps = 1000.0 / (inference_ms + render_ms) if inference_ms + render_ms > 0 else 0.0
        frame_row = {
            "frame_index": frame_index,
            "video_time_seconds": sync.video_time_seconds,
            "plant_count": len(detections),
            **diagnostics.to_dict(),
            "tile_count": tile_count,
            "inference_ms": inference_ms,
            "render_ms": render_ms,
            "effective_fps": effective_fps,
            "srt_index": sync.srt_index,
            "sync_delta_seconds": sync.sync_delta_seconds,
            "sync_status": sync.sync_status,
        }
        self.frame_writer.writerow({column: frame_row.get(column) for column in FRAME_SUMMARY_COLUMNS})
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
        self.telemetry_writer.writerow(telemetry_row)
        json.dump(
            {
                "frame_index": frame_index,
                "video_time_seconds": sync.video_time_seconds,
                "telemetry": telemetry.to_dict() if telemetry else {},
                "sync": {
                    "srt_index": sync.srt_index,
                    "srt_start_seconds": sync.srt_start_seconds,
                    "srt_end_seconds": sync.srt_end_seconds,
                    "sync_delta_seconds": sync.sync_delta_seconds,
                    "sync_status": sync.sync_status,
                },
                "merge_diagnostics": diagnostics.to_dict(),
                "detections": [detection.to_dict() for detection in detections],
            },
            self.jsonl,
            ensure_ascii=False,
        )
        self.jsonl.write("\n")
        if self.video_writer:
            self.video_writer.write(annotated_frame)
        self.processed_frames += 1
        self.total_detections += len(detections)

    def write_video_frame(self, frame: np.ndarray) -> None:
        if self.video_writer:
            self.video_writer.write(frame)

    def snapshot(self, frame: np.ndarray, frame_index: int) -> Path:
        path = self.snapshots_dir / f"frame_{frame_index:06d}.jpg"
        if not cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise OSError(f"Gagal menulis snapshot: {path}")
        return path

    def copy_srt_report(self, source: str | Path) -> None:
        shutil.copy2(source, self.run_dir / "srt_parse_report.json")

    def close(self, summary: dict[str, Any] | None = None) -> dict[str, Any]:
        video_validation = None
        if self.video_writer:
            self.video_writer.close()
            video_validation = self.video_writer.validate()
        for handle in self.files:
            handle.flush()
            handle.close()
        self.run_logger.removeHandler(self.log_handler)
        self.log_handler.close()
        self.runtime_log.write(
            f"{datetime.now().isoformat()} run_completed processed_frames={self.processed_frames} "
            f"detections={self.total_detections}\n"
        )
        self.runtime_log.flush()
        self.runtime_log.close()
        self.run_summary = {
            "status": "completed",
            "processed_frames": self.processed_frames,
            "total_detections": self.total_detections,
            "video_validation": video_validation,
            "codec_report": ffmpeg_codec_report(),
            "audio_preserved": False,
            **(summary or {}),
        }
        self._write_json(self.run_summary, "run_summary.json")
        return self.run_summary

    def abort(self, reason: str) -> None:
        if self.run_summary:
            return
        video_error = None
        try:
            if self.video_writer:
                self.video_writer.close()
        except Exception as exc:  # Preserve the original pipeline failure.
            video_error = f"{type(exc).__name__}: {exc}"
        for handle in self.files:
            if not handle.closed:
                handle.flush()
                handle.close()
        self.run_logger.removeHandler(self.log_handler)
        self.log_handler.close()
        if not self.runtime_log.closed:
            self.runtime_log.write(
                f"{datetime.now().isoformat()} run_failed reason={reason}\n"
            )
            self.runtime_log.flush()
            self.runtime_log.close()
        self.run_summary = {
            "status": "failed",
            "reason": reason,
            "video_cleanup_error": video_error,
            "processed_frames": self.processed_frames,
            "total_detections": self.total_detections,
        }
        self._write_json(self.run_summary, "run_summary.json")
