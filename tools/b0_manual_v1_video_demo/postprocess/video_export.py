"""Offline, cache-only annotated video export for a completed full-pipeline run."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Sequence, TextIO

import cv2
import numpy as np

from .common import read_json, sha256_file, write_json


DEFAULT_VIDEO_NAME = "full_pipeline_review_sampled.mp4"
VIDEO_MANIFEST_NAME = "video_export_manifest.json"
FRAME_MANIFEST_NAME = "frame_render_manifest.csv"
VIDEO_LOG_NAME = "video_export.log"
CONTACT_SHEET_NAME = "video_contact_sheet.jpg"
LOW_CONFIDENCE_THRESHOLD = 0.30

FRAME_RENDER_COLUMNS = [
    "rendered_frame_index",
    "source_frame_index",
    "video_time_seconds",
    "raw_mask_file",
    "processed_mask_file",
    "raw_mask_missing",
    "processed_mask_missing",
    "segmentation_instance_count",
    "safe_observations",
    "boundary_review_observations",
    "outside_rejected_observations",
    "low_confidence_observations",
    "total_observations",
    "drone_latitude",
    "drone_longitude",
    "relative_altitude",
    "absolute_altitude",
    "heading",
    "gimbal_pitch",
    "srt_sync_status",
]


@dataclass(frozen=True)
class VideoExportOptions:
    resolution: str = "review"
    width: int = 1920
    codec: str = "auto"
    crf: int = 20
    preset: str = "medium"
    mask_alpha: float = 0.25
    plant_style: str = "points"
    keep_audio: bool = False
    output: Path | None = None


class VideoProgress:
    """TTY/non-TTY progress with elapsed time, encoding speed, and ETA."""

    def __init__(self, total: int, log: TextIO) -> None:
        self.total = int(total)
        self.log = log
        self.started = time.perf_counter()
        self.last_bucket = -1
        self.last_print = 0.0
        self.tty = bool(sys.stderr.isatty())

    def update(
        self,
        completed: int,
        *,
        safe: int,
        boundary: int,
        rejected: int,
    ) -> None:
        now = time.perf_counter()
        elapsed = max(now - self.started, 1e-9)
        percent = 100.0 * completed / max(1, self.total)
        speed = completed / elapsed
        eta = (self.total - completed) / speed if speed > 0 else math.inf
        bucket = int(percent // 5)
        if not (
            self.tty
            or completed >= self.total
            or bucket > self.last_bucket
            or now - self.last_print >= 30
        ):
            return
        message = (
            f"[Video Export] Rendering {completed}/{self.total} ({percent:.1f}%) "
            f"elapsed={_duration(elapsed)} speed={speed:.2f} frame/s "
            f"ETA={_duration(eta) if math.isfinite(eta) else '--'} "
            f"safe={safe} boundary={boundary} rejected={rejected}"
        )
        print(
            message,
            end="\r" if self.tty and completed < self.total else "\n",
            file=sys.stderr,
            flush=True,
        )
        self.log.write(message + "\n")
        self.log.flush()
        self.last_bucket = bucket
        self.last_print = now


class ObservationStream:
    """Single-pass observation reader grouped by monotonically increasing frame."""

    def __init__(self, path: Path) -> None:
        self.handle = path.open(newline="", encoding="utf-8")
        self.reader = csv.DictReader(self.handle)
        required = {
            "frame_index",
            "confidence",
            "x1",
            "y1",
            "x2",
            "y2",
            "center_x",
            "center_y",
            "segmentation_gate_status",
        }
        if not self.reader.fieldnames or not required.issubset(self.reader.fieldnames):
            self.handle.close()
            raise ValueError(f"Observation schema is incomplete: {path}")
        self.current = next(self.reader, None)
        self.previous_frame = -1

    def take(self, frame_index: int) -> list[tuple[float | str, ...]]:
        observations: list[tuple[float | str, ...]] = []
        while self.current is not None:
            current_frame = int(self.current["frame_index"])
            if current_frame < self.previous_frame:
                raise ValueError("plant_observations.csv is not sorted by frame_index")
            self.previous_frame = current_frame
            if current_frame < frame_index:
                raise ValueError(
                    f"Unexpected cached observation for unrendered frame {current_frame}"
                )
            if current_frame > frame_index:
                break
            row = self.current
            observations.append(
                (
                    float(row["confidence"]),
                    float(row["x1"]),
                    float(row["y1"]),
                    float(row["x2"]),
                    float(row["y2"]),
                    float(row["center_x"]),
                    float(row["center_y"]),
                    row["segmentation_gate_status"],
                )
            )
            self.current = next(self.reader, None)
        return observations

    def assert_exhausted(self) -> None:
        if self.current is not None:
            raise ValueError(
                "Cached observations remain after the final processed frame; "
                "frame mapping is inconsistent"
            )

    def close(self) -> None:
        self.handle.close()

    def __enter__(self) -> ObservationStream:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_fraction(value: str | float | int) -> float:
    if isinstance(value, (float, int)):
        return float(value)
    if not value or value == "0/0":
        return 0.0
    return float(Fraction(value))


def _output_fps(source_fps: float, stride: int) -> float:
    if source_fps <= 0 or stride <= 0:
        raise ValueError("source_fps and stride must be positive")
    return source_fps / stride


def _scaled_output_size(
    source_width: int,
    source_height: int,
    resolution: str,
    review_width: int,
) -> tuple[int, int]:
    if source_width <= 0 or source_height <= 0:
        raise ValueError("Source resolution must be positive")
    if resolution == "source":
        width, height = source_width, source_height
    elif resolution == "review":
        if review_width <= 0:
            raise ValueError("--video-width must be positive")
        width = min(source_width, review_width)
        height = int(round(source_height * width / source_width))
    else:
        raise ValueError(f"Unsupported video resolution mode: {resolution}")
    width -= width % 2
    height -= height % 2
    if width < 2 or height < 2:
        raise ValueError("Resolved video resolution is too small")
    return width, height


def _expected_frame_mapping(
    source_frame_count: int,
    stride: int,
    cached_indices: Sequence[int],
) -> list[int]:
    expected = list(range(0, source_frame_count, stride))[: len(cached_indices)]
    actual = [int(value) for value in cached_indices]
    if actual != expected:
        raise ValueError(
            "Cached processed-frame mapping does not match source stride; "
            "video export will not invent or interpolate frames"
        )
    return actual


def _scale_points(
    points: np.ndarray,
    source_size: tuple[int, int],
    output_size: tuple[int, int],
) -> np.ndarray:
    result = np.asarray(points, dtype=np.float64).copy()
    if result.size == 0:
        return result.reshape((-1, 2))
    source_width, source_height = source_size
    output_width, output_height = output_size
    result[:, 0] *= output_width / source_width
    result[:, 1] *= output_height / source_height
    return result


def _resize_mask(
    mask: np.ndarray,
    source_size: tuple[int, int],
    output_size: tuple[int, int],
) -> np.ndarray:
    source_width, source_height = source_size
    if mask.ndim != 2 or mask.shape != (source_height, source_width):
        raise ValueError(
            f"Mask/frame alignment mismatch: mask={mask.shape}, "
            f"source={(source_height, source_width)}"
        )
    output_width, output_height = output_size
    if output_size == source_size:
        return mask
    return cv2.resize(mask, (output_width, output_height), interpolation=cv2.INTER_NEAREST)


def _load_mask(
    path: Path,
    source_size: tuple[int, int],
    output_size: tuple[int, int],
) -> tuple[np.ndarray | None, bool]:
    if not path.is_file():
        return None, True
    mask = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        return None, True
    return _resize_mask(mask, source_size, output_size), False


def _read_csv_by_frame(path: Path) -> dict[int, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = csv.DictReader(handle)
        if not rows.fieldnames or "frame_index" not in rows.fieldnames:
            raise ValueError(f"Missing frame_index column: {path}")
        return {int(row["frame_index"]): row for row in rows}


def _read_srt_heading(path: Path) -> dict[int, float | None]:
    if not path.is_file():
        return {}
    result: dict[int, float | None] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            value = row.get("heading", "")
            result[int(row["index"])] = float(value) if value else None
    return result


def _probe_media(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise FileNotFoundError("ffprobe is required for factual MP4 validation")
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            (
                "stream=index,codec_type,codec_name,pix_fmt,width,height,"
                "r_frame_rate,avg_frame_rate,duration,nb_frames"
            ),
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    video_streams = [
        stream
        for stream in payload.get("streams", [])
        if stream.get("codec_type") == "video" and stream.get("width") and stream.get("height")
    ]
    if not video_streams:
        raise ValueError(f"No decodable video stream found: {path}")
    video = max(video_streams, key=lambda stream: int(stream["width"]) * int(stream["height"]))
    fps = _parse_fraction(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/0")
    duration = float(
        video.get("duration")
        or payload.get("format", {}).get("duration")
        or 0.0
    )
    frame_count_value = video.get("nb_frames")
    frame_count = int(frame_count_value) if str(frame_count_value).isdigit() else 0
    audio = [
        stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"
    ]
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": fps,
        "duration_seconds": duration,
        "frame_count": frame_count,
        "codec": video.get("codec_name"),
        "pixel_format": video.get("pix_fmt"),
        "audio_stream_count": len(audio),
        "audio_codecs": [stream.get("codec_name") for stream in audio],
        "raw": payload,
    }


def _available_ffmpeg_encoders() -> tuple[str | None, set[str]]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None, set()
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        check=True,
        capture_output=True,
        text=True,
    )
    encoders: set[str] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0].startswith("V"):
            encoders.add(fields[1])
    return ffmpeg, encoders


def _select_encoder(requested: str) -> tuple[str | None, str]:
    ffmpeg, encoders = _available_ffmpeg_encoders()
    if requested == "auto":
        for candidate in ("libx264", "h264_videotoolbox", "mpeg4"):
            if candidate in encoders:
                return ffmpeg, candidate
        return None, "opencv_mp4v"
    if requested == "opencv_mp4v":
        return None, requested
    if not ffmpeg or requested not in encoders:
        raise ValueError(f"Requested FFmpeg encoder is unavailable: {requested}")
    return ffmpeg, requested


class _FrameEncoder:
    def __init__(
        self,
        path: Path,
        width: int,
        height: int,
        fps: float,
        codec: str,
        crf: int,
        preset: str,
        log: TextIO,
    ) -> None:
        self.path = path
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_count = 0
        self.ffmpeg, self.encoder = _select_encoder(codec)
        self.process: subprocess.Popen[bytes] | None = None
        self.writer: cv2.VideoWriter | None = None
        self.faststart_requested = False
        if self.ffmpeg:
            command = [
                self.ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "-video_size",
                f"{width}x{height}",
                "-framerate",
                f"{fps:.12f}",
                "-i",
                "-",
                "-an",
                "-c:v",
                self.encoder,
            ]
            if self.encoder == "libx264":
                command.extend(["-preset", preset, "-crf", str(crf)])
            elif self.encoder == "h264_videotoolbox":
                command.extend(["-q:v", "65"])
            elif self.encoder == "mpeg4":
                command.extend(["-q:v", "3"])
            command.extend(
                [
                    "-pix_fmt",
                    "yuv420p",
                    "-fps_mode",
                    "cfr",
                    "-movflags",
                    "+faststart",
                    str(path),
                ]
            )
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=log)
            self.faststart_requested = True
        else:
            writer = cv2.VideoWriter(
                str(path),
                cv2.VideoWriter_fourcc(*"mp4v"),
                fps,
                (width, height),
            )
            if not writer.isOpened():
                writer.release()
                raise OSError("Neither FFmpeg H.264 nor OpenCV MP4 fallback could be opened")
            self.writer = writer

    def write(self, frame: np.ndarray) -> None:
        if frame.shape != (self.height, self.width, 3):
            raise ValueError(
                f"Rendered frame shape changed: {frame.shape}; "
                f"expected {(self.height, self.width, 3)}"
            )
        if self.process:
            if not self.process.stdin:
                raise BrokenPipeError("FFmpeg stdin is unavailable")
            self.process.stdin.write(np.ascontiguousarray(frame).tobytes())
        elif self.writer:
            self.writer.write(frame)
        else:
            raise RuntimeError("Video encoder is closed")
        self.frame_count += 1

    def close(self) -> None:
        if self.process:
            if self.process.stdin:
                self.process.stdin.close()
            return_code = self.process.wait(timeout=300)
            self.process = None
            if return_code != 0:
                raise OSError(f"FFmpeg encoder exited with status {return_code}")
        if self.writer:
            self.writer.release()
            self.writer = None

    def abort(self) -> None:
        if self.process:
            if self.process.stdin:
                self.process.stdin.close()
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
        if self.writer:
            self.writer.release()
            self.writer = None


def _mask_contours(mask: np.ndarray) -> list[np.ndarray]:
    contours, _ = cv2.findContours(
        (mask > 0).astype(np.uint8),
        cv2.RETR_LIST,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    return contours


def _blend_mask(frame: np.ndarray, mask: np.ndarray, color: tuple[int, int, int], alpha: float) -> None:
    overlay = frame.copy()
    overlay[mask] = color
    cv2.addWeighted(overlay, alpha, frame, 1.0 - alpha, 0.0, dst=frame)


def _draw_segmentation(
    frame: np.ndarray,
    raw_mask: np.ndarray | None,
    processed_mask: np.ndarray | None,
    mask_alpha: float,
    boundary_buffer_output_px: float,
) -> None:
    line_width = max(1, round(frame.shape[1] / 960))
    if processed_mask is not None:
        processed = processed_mask > 0
        _blend_mask(frame, processed, (35, 190, 35), mask_alpha)
        distance = cv2.distanceTransform(processed.astype(np.uint8), cv2.DIST_L2, 3)
        safe = distance > boundary_buffer_output_px
        corridor = processed & ~safe
        if np.any(corridor):
            _blend_mask(frame, corridor, (0, 210, 255), min(0.45, mask_alpha + 0.10))
        cv2.drawContours(
            frame,
            _mask_contours(processed_mask),
            -1,
            (40, 255, 40),
            line_width,
            cv2.LINE_AA,
        )
        cv2.drawContours(
            frame,
            _mask_contours(safe.astype(np.uint8) * 255),
            -1,
            (80, 255, 80),
            line_width,
            cv2.LINE_AA,
        )
    if raw_mask is not None:
        cv2.drawContours(
            frame,
            _mask_contours(raw_mask),
            -1,
            (255, 255, 0),
            line_width,
            cv2.LINE_AA,
        )


def _observation_counts(
    observations: Sequence[tuple[float | str, ...]],
) -> dict[str, int]:
    counts = {
        "accepted": 0,
        "boundary_review": 0,
        "outside_rejected": 0,
        "low_confidence": 0,
    }
    for observation in observations:
        confidence = float(observation[0])
        status = str(observation[7])
        if status not in counts:
            raise ValueError(f"Unknown segmentation gate status: {status}")
        counts[status] += 1
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            counts["low_confidence"] += 1
    return counts


def _draw_observations(
    frame: np.ndarray,
    observations: Sequence[tuple[float | str, ...]],
    source_size: tuple[int, int],
    style: str,
) -> None:
    if style == "none" or not observations:
        return
    output_size = (frame.shape[1], frame.shape[0])
    scale_x = output_size[0] / source_size[0]
    scale_y = output_size[1] / source_size[1]
    colors = {
        "accepted": (30, 255, 30),
        "boundary_review": (0, 165, 255),
        "outside_rejected": (0, 0, 255),
        "low_confidence": (255, 0, 255),
    }
    if style == "boxes":
        thickness = max(1, round(output_size[0] / 1920))
        for confidence, x1, y1, x2, y2, _, _, status in observations:
            category = (
                "low_confidence"
                if float(confidence) < LOW_CONFIDENCE_THRESHOLD
                else str(status)
            )
            cv2.rectangle(
                frame,
                (round(float(x1) * scale_x), round(float(y1) * scale_y)),
                (round(float(x2) * scale_x), round(float(y2) * scale_y)),
                colors[category],
                thickness,
                cv2.LINE_AA,
            )
        return
    radius = max(1, round(2 * output_size[0] / 1920))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (2 * radius + 1, 2 * radius + 1),
    )
    for category, color in colors.items():
        points = [
            (float(row[5]), float(row[6]))
            for row in observations
            if (
                category == "low_confidence"
                and float(row[0]) < LOW_CONFIDENCE_THRESHOLD
            )
            or (
                category != "low_confidence"
                and float(row[0]) >= LOW_CONFIDENCE_THRESHOLD
                and str(row[7]) == category
            )
        ]
        if not points:
            continue
        scaled = np.rint(
            _scale_points(np.asarray(points), source_size, output_size)
        ).astype(np.int32)
        scaled[:, 0] = np.clip(scaled[:, 0], 0, output_size[0] - 1)
        scaled[:, 1] = np.clip(scaled[:, 1], 0, output_size[1] - 1)
        marker = np.zeros((output_size[1], output_size[0]), dtype=np.uint8)
        marker[scaled[:, 1], scaled[:, 0]] = 255
        marker = cv2.dilate(marker, kernel)
        frame[marker > 0] = color


def _format_video_time(seconds: float) -> str:
    milliseconds = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _number(value: str | float | int | None, precision: int = 2) -> str:
    if value in (None, ""):
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(number):
        return "n/a"
    return f"{number:.{precision}f}"


def _flight_label(run_manifest: dict[str, Any], run_dir: Path) -> str:
    if "flight1" in run_dir.name.casefold():
        return "Flight 1"
    flight = Path(run_manifest["inputs"].get("flight_dir", "Flight")).name
    return flight or "Flight"


def _draw_information_panel(
    frame: np.ndarray,
    *,
    flight_label: str,
    rendered_index: int,
    rendered_count: int,
    source_frame_index: int,
    source_frame_count: int,
    video_time_seconds: float,
    stride: int,
    segmentation_instances: int,
    counts: dict[str, int],
    confirmed_plants: int,
    gap_candidates: int,
    gap_groups: int,
    georeference_status: str,
    telemetry: dict[str, str],
    heading: float | None,
    plant_style: str,
) -> None:
    height, width = frame.shape[:2]
    if width < 640 or height < 360:
        cv2.putText(
            frame,
            "DEMO ONLY | Sampled inference review",
            (5, max(15, height - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.32,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        return
    scale = max(0.52, min(1.05, width / 1920 * 0.62))
    line_height = max(19, round(28 * scale))
    margin = max(12, round(18 * width / 1920))
    lines = [
        (f"{flight_label} | DEMO ONLY", (255, 255, 255)),
        ("Sampled inference review", (255, 255, 255)),
        (
            f"Processed: {rendered_index + 1}/{rendered_count} | "
            f"Source: {source_frame_index}/{source_frame_count}",
            (255, 255, 255),
        ),
        (
            f"Timestamp: {_format_video_time(video_time_seconds)} | Stride: {stride}",
            (255, 255, 255),
        ),
        ("No inference was performed on skipped frames", (120, 220, 255)),
        (
            f"Seg instances: {segmentation_instances} | "
            f"Safe: {counts['accepted']} | Boundary: {counts['boundary_review']} | "
            f"Rejected: {counts['outside_rejected']}",
            (255, 255, 255),
        ),
        (
            f"Confirmed plants/run: {confirmed_plants:,} | "
            f"Gap candidates: {gap_candidates:,} | Groups: {gap_groups:,}",
            (255, 255, 255),
        ),
        (f"Georeferencing: {georeference_status}", (150, 210, 255)),
        ("Drone telemetry", (255, 255, 255)),
        (
            f"Alt rel: {_number(telemetry.get('relative_altitude'))} m | "
            f"Heading: {_number(heading, 1)} deg | "
            f"Gimbal pitch: {_number(telemetry.get('gimbal_pitch'), 1)} deg",
            (255, 255, 255),
        ),
        (
            f"Drone telemetry lat/lon: "
            f"{_number(telemetry.get('drone_latitude'), 6)}, "
            f"{_number(telemetry.get('drone_longitude'), 6)}",
            (255, 255, 255),
        ),
    ]
    panel_width = min(width - 2 * margin, round(880 * width / 1920))
    panel_height = margin * 2 + line_height * len(lines)
    overlay = frame.copy()
    cv2.rectangle(
        overlay,
        (margin, margin),
        (margin + panel_width, margin + panel_height),
        (0, 0, 0),
        -1,
    )
    cv2.addWeighted(overlay, 0.62, frame, 0.38, 0, dst=frame)
    x = margin * 2
    y = margin * 2 + line_height
    for text, color in lines:
        cv2.putText(
            frame,
            text,
            (x, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            color,
            max(1, round(scale * 1.6)),
            cv2.LINE_AA,
        )
        y += line_height
    legend = [
        ("Plant safe", (30, 255, 30)),
        ("Boundary review", (0, 165, 255)),
        ("Outside/rejected", (0, 0, 255)),
        (f"Low confidence < {LOW_CONFIDENCE_THRESHOLD:.2f}", (255, 0, 255)),
        ("Raw mask outline", (255, 255, 0)),
        ("Boundary corridor", (0, 210, 255)),
    ]
    if plant_style == "none":
        legend = legend[-2:]
    legend_x = width - round(300 * width / 1920)
    legend_y = height - margin - len(legend) * line_height
    for label, color in legend:
        cv2.circle(frame, (legend_x, legend_y - 5), max(3, round(scale * 5)), color, -1)
        cv2.putText(
            frame,
            label,
            (legend_x + round(18 * scale), legend_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale * 0.85,
            (255, 255, 255),
            max(1, round(scale)),
            cv2.LINE_AA,
        )
        legend_y += line_height


def _mp4_faststart(path: Path) -> bool:
    positions: dict[bytes, int] = {}
    with path.open("rb") as handle:
        offset = 0
        file_size = path.stat().st_size
        while offset + 8 <= file_size:
            handle.seek(offset)
            header = handle.read(8)
            if len(header) != 8:
                break
            size = int.from_bytes(header[:4], "big")
            atom = header[4:8]
            header_size = 8
            if size == 1:
                extended = handle.read(8)
                if len(extended) != 8:
                    break
                size = int.from_bytes(extended, "big")
                header_size = 16
            elif size == 0:
                size = file_size - offset
            if size < header_size:
                break
            positions.setdefault(atom, offset)
            offset += size
    return b"moov" in positions and b"mdat" in positions and positions[b"moov"] < positions[b"mdat"]


def _decode_validation_frames(
    path: Path,
    frame_count: int,
) -> tuple[list[dict[str, Any]], list[np.ndarray]]:
    selected = [0, frame_count // 2, frame_count - 1]
    results: list[dict[str, Any]] = []
    images: list[np.ndarray] = []
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise OSError(f"OpenCV could not open encoded video: {path}")
    for index in selected:
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        stddev = float(frame.std()) if ok and frame is not None else 0.0
        mean = float(frame.mean()) if ok and frame is not None else 0.0
        results.append(
            {
                "frame_index": index,
                "decoded": bool(ok and frame is not None),
                "mean": mean,
                "stddev": stddev,
                "non_blank": bool(ok and frame is not None and stddev > 1.0 and mean > 1.0),
            }
        )
        if ok and frame is not None:
            images.append(frame)
    capture.release()
    if len(images) != 3:
        raise OSError("First/middle/last frame validation decode failed")
    return results, images


def _validate_video(
    path: Path,
    *,
    expected_size: tuple[int, int],
    expected_fps: float,
    expected_frames: int,
    source_duration: float,
    faststart_required: bool,
) -> tuple[dict[str, Any], list[np.ndarray]]:
    probe = _probe_media(path)
    decoded, images = _decode_validation_frames(path, expected_frames)
    faststart = _mp4_faststart(path)
    frame_count = probe["frame_count"]
    if frame_count == 0:
        capture = cv2.VideoCapture(str(path))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        capture.release()
    quicktime_codec = probe["codec"] in {"h264", "mpeg4"}
    passed = bool(
        probe["width"] == expected_size[0]
        and probe["height"] == expected_size[1]
        and frame_count == expected_frames
        and abs(probe["fps"] - expected_fps) <= 0.02
        and abs(probe["duration_seconds"] - source_duration)
        <= max(0.5, 2.0 / expected_fps)
        and probe["pixel_format"] == "yuv420p"
        and quicktime_codec
        and all(item["non_blank"] for item in decoded)
        and (faststart or not faststart_required)
    )
    report = {
        "passed": passed,
        "codec": probe["codec"],
        "pixel_format": probe["pixel_format"],
        "width": probe["width"],
        "height": probe["height"],
        "fps": probe["fps"],
        "frame_count": frame_count,
        "duration_seconds": probe["duration_seconds"],
        "quicktime_compatible_codec": quicktime_codec,
        "faststart_detected": faststart,
        "selected_frame_decode": decoded,
    }
    if not passed:
        raise OSError(f"Final MP4 validation failed: {json.dumps(report, sort_keys=True)}")
    return report, images


def _write_contact_sheet(path: Path, images: Sequence[np.ndarray]) -> None:
    labels = ("First frame", "Middle frame", "Last frame")
    panels: list[np.ndarray] = []
    target_width = 600
    for label, image in zip(labels, images, strict=True):
        height = round(image.shape[0] * target_width / image.shape[1])
        panel = cv2.resize(image, (target_width, height), interpolation=cv2.INTER_AREA)
        overlay = panel.copy()
        cv2.rectangle(overlay, (0, 0), (target_width, 42), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.65, panel, 0.35, 0, dst=panel)
        cv2.putText(
            panel,
            label,
            (12, 29),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        panels.append(panel)
    sheet = np.concatenate(panels, axis=1)
    if not cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 90]):
        raise OSError(f"Could not write contact sheet: {path}")


def _mux_audio(
    ffmpeg: str,
    rendered_video: Path,
    source_video: Path,
    output: Path,
    log: TextIO,
) -> tuple[str, bool]:
    base = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-i",
        str(rendered_video),
        "-i",
        str(source_video),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output),
    ]
    result = subprocess.run(base, stdout=log, stderr=log)
    if result.returncode == 0:
        return "copied", True
    fallback = base[:]
    codec_index = fallback.index("copy", fallback.index("-c:a"))
    fallback[codec_index : codec_index + 1] = ["aac", "-b:a", "192k"]
    result = subprocess.run(fallback, stdout=log, stderr=log)
    if result.returncode != 0:
        raise OSError("Optional audio mux failed with copy and AAC fallback")
    return "transcoded_aac_video_stream_copied", True


def _resolve_mask_path(run_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else run_dir / path


def _source_run_totals(run_dir: Path) -> dict[str, int]:
    qa = read_json(run_dir / "qa_summary.json")
    counts = qa["current_counts"]
    return {
        "confirmed_plants": int(counts["confirmed_unique_plants"]),
        "gap_candidates": int(counts["missing_plants"]),
        "gap_groups": int(counts["gap_groups"]),
    }


def _options_payload(options: VideoExportOptions) -> dict[str, Any]:
    return {
        "resolution": options.resolution,
        "review_width": options.width,
        "codec": options.codec,
        "crf": options.crf,
        "preset": options.preset,
        "mask_alpha": options.mask_alpha,
        "plant_style": options.plant_style,
        "keep_audio": options.keep_audio,
    }


def _existing_export_is_complete(
    video_dir: Path,
    output_path: Path,
    requested_options: dict[str, Any] | None = None,
) -> bool:
    manifest_path = video_dir / VIDEO_MANIFEST_NAME
    if not manifest_path.is_file() or not output_path.is_file():
        return False
    try:
        manifest = read_json(manifest_path)
        return bool(
            manifest.get("status") == "complete"
            and manifest.get("validation", {}).get("passed")
            and Path(manifest["output_video"]).resolve() == output_path.resolve()
            and manifest.get("output_video_sha256") == sha256_file(output_path)
            and (
                requested_options is None
                or manifest.get("requested_options") == requested_options
            )
        )
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return False


def _cleanup_owned_partials(video_dir: Path, output_parent: Path) -> None:
    for directory in {video_dir.resolve(), output_parent.resolve()}:
        if not directory.is_dir():
            continue
        for path in directory.glob(".video_export_*.partial.*"):
            if path.is_file():
                path.unlink()


def export_review_video(
    run_dir: Path,
    options: VideoExportOptions | None = None,
    *,
    resume: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Render sampled cached inference frames without loading either model."""

    if resume and force:
        raise ValueError("--resume and --force are mutually exclusive")
    options = options or VideoExportOptions()
    if options.resolution not in {"review", "source"}:
        raise ValueError("--video-resolution must be review or source")
    if options.plant_style not in {"points", "boxes", "none"}:
        raise ValueError("--video-plant-style must be points, boxes, or none")
    if not 0.0 <= options.mask_alpha <= 1.0:
        raise ValueError("--video-mask-alpha must be between 0 and 1")
    if not 0 <= options.crf <= 51:
        raise ValueError("--video-crf must be between 0 and 51")

    run_dir = run_dir.expanduser().resolve()
    required = [
        "run_manifest.json",
        "frame_manifest.csv",
        "segmentation_frame_summary.csv",
        "plant_observations.csv",
        "segmentation_gating_report.json",
        "qa_summary.json",
        "georeference_report.json",
    ]
    missing = [name for name in required if not (run_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Completed-run video cache is incomplete: {missing}")

    video_dir = run_dir / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    output_path = (
        options.output.expanduser()
        if options.output
        else video_dir / DEFAULT_VIDEO_NAME
    )
    if not output_path.is_absolute():
        output_path = video_dir / output_path
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    requested_options = _options_payload(options)
    if resume and _existing_export_is_complete(
        video_dir,
        output_path,
        requested_options,
    ):
        message = "[Video Export] Complete artifact checksum matches; skipping render"
        print(message, file=sys.stderr, flush=True)
        manifest = read_json(video_dir / VIDEO_MANIFEST_NAME)
        manifest["skipped_by_resume"] = True
        return manifest
    if not resume and not force and (
        output_path.exists() or (video_dir / VIDEO_MANIFEST_NAME).exists()
    ):
        raise FileExistsError(
            f"Video export already exists: {output_path}. Use --resume or --force."
        )

    _cleanup_owned_partials(video_dir, output_path.parent)
    token = uuid.uuid4().hex
    temporary_video = output_path.parent / f".video_export_{token}.partial.mp4"
    muxed_video = output_path.parent / f".video_export_{token}.partial.mux.mp4"
    temporary_frames = video_dir / f".video_export_{token}.partial.csv"
    temporary_log = video_dir / f".video_export_{token}.partial.log"
    temporary_contact = video_dir / f".video_export_{token}.partial.jpg"
    temporary_paths = {
        temporary_video,
        muxed_video,
        temporary_frames,
        temporary_log,
        temporary_contact,
    }

    run_manifest_path = run_dir / "run_manifest.json"
    run_manifest_sha256 = sha256_file(run_manifest_path)
    run_manifest = read_json(run_manifest_path)
    source_video = Path(run_manifest["inputs"]["video"]).expanduser().resolve()
    if not source_video.is_file():
        raise FileNotFoundError(f"Source video is unavailable: {source_video}")
    source_probe = _probe_media(source_video)
    sampling = run_manifest["frame_sampling"]
    source_frame_count = int(sampling["source_frame_count"])
    rendered_frame_count = int(sampling["processed_frame_count"])
    stride = int(sampling["stride"])
    frame_indices = _expected_frame_mapping(
        source_frame_count,
        stride,
        [int(value) for value in sampling["frame_indices"]],
    )
    if len(frame_indices) != rendered_frame_count:
        raise ValueError("Processed frame count does not match cached frame indices")
    if source_probe["frame_count"] and source_probe["frame_count"] != source_frame_count:
        raise ValueError("Source video frame count changed since the inference run")
    source_size = (source_probe["width"], source_probe["height"])
    output_size = _scaled_output_size(
        source_probe["width"],
        source_probe["height"],
        options.resolution,
        options.width,
    )
    output_fps = _output_fps(source_probe["fps"], stride)

    frames = _read_csv_by_frame(run_dir / "frame_manifest.csv")
    segmentations = _read_csv_by_frame(run_dir / "segmentation_frame_summary.csv")
    headings = _read_srt_heading(run_dir / "srt_parsed.csv")
    if set(frame_indices) != set(frames) or set(frame_indices) != set(segmentations):
        raise ValueError("Frame, telemetry, and segmentation cache mapping is inconsistent")
    boundary_buffer = float(
        read_json(run_dir / "segmentation_gating_report.json")["boundary_buffer_px"]
    )
    boundary_buffer_output = boundary_buffer * min(
        output_size[0] / source_size[0],
        output_size[1] / source_size[1],
    )
    totals = _source_run_totals(run_dir)
    georeference_status = read_json(run_dir / "georeference_report.json")["status"]
    flight_label = _flight_label(run_manifest, run_dir)
    ffmpeg, _ = _available_ffmpeg_encoders()

    started = time.perf_counter()
    encoder: _FrameEncoder | None = None
    rendered_totals = {
        "accepted": 0,
        "boundary_review": 0,
        "outside_rejected": 0,
        "low_confidence": 0,
        "observations": 0,
    }
    missing_masks = {"raw": 0, "processed": 0}
    audio_status = "not_requested"
    audio_preserved = False
    final_candidate = temporary_video
    selected_encoder = ""
    faststart_required = False
    capture: cv2.VideoCapture | None = None

    try:
        with temporary_log.open("w", encoding="utf-8", buffering=1) as log:
            def announce(message: str) -> None:
                print(message, file=sys.stderr, flush=True)
                log.write(f"{_timestamp()} {message}\n")
                log.flush()

            announce("[Video Export] Auditing cached artifacts")
            announce(
                "[Video Export] Cache-only mode: detector_loaded=false "
                "segmenter_loaded=false inference_performed=false"
            )
            announce(
                f"[Video Export] Encoding {rendered_frame_count} sampled frames "
                f"at {output_fps:.9f} FPS"
            )
            encoder = _FrameEncoder(
                temporary_video,
                output_size[0],
                output_size[1],
                output_fps,
                options.codec,
                options.crf,
                options.preset,
                log,
            )
            selected_encoder = encoder.encoder
            faststart_required = encoder.faststart_requested
            progress = VideoProgress(rendered_frame_count, log)
            capture = cv2.VideoCapture(str(source_video))
            if not capture.isOpened():
                raise OSError(f"Could not open source video: {source_video}")
            target_position = 0
            next_target = frame_indices[target_position]
            with (
                temporary_frames.open("w", newline="", encoding="utf-8") as frame_handle,
                ObservationStream(run_dir / "plant_observations.csv") as observation_stream,
            ):
                frame_writer = csv.DictWriter(frame_handle, fieldnames=FRAME_RENDER_COLUMNS)
                frame_writer.writeheader()
                for source_index in range(frame_indices[-1] + 1):
                    ok, source_frame = capture.read()
                    if not ok or source_frame is None:
                        raise OSError(f"Could not decode source frame {source_index}")
                    if source_index != next_target:
                        continue
                    frame_meta = frames[source_index]
                    segmentation = segmentations[source_index]
                    observations = observation_stream.take(source_index)
                    counts = _observation_counts(observations)
                    raw_path = _resolve_mask_path(run_dir, segmentation["raw_mask_file"])
                    processed_path = _resolve_mask_path(
                        run_dir,
                        segmentation["processed_mask_file"],
                    )
                    raw_mask, raw_missing = _load_mask(raw_path, source_size, output_size)
                    processed_mask, processed_missing = _load_mask(
                        processed_path,
                        source_size,
                        output_size,
                    )
                    missing_masks["raw"] += int(raw_missing)
                    missing_masks["processed"] += int(processed_missing)
                    rendered = (
                        source_frame
                        if output_size == source_size
                        else cv2.resize(source_frame, output_size, interpolation=cv2.INTER_AREA)
                    )
                    _draw_segmentation(
                        rendered,
                        raw_mask,
                        processed_mask,
                        options.mask_alpha,
                        boundary_buffer_output,
                    )
                    _draw_observations(
                        rendered,
                        observations,
                        source_size,
                        options.plant_style,
                    )
                    srt_index = int(frame_meta["srt_index"])
                    _draw_information_panel(
                        rendered,
                        flight_label=flight_label,
                        rendered_index=target_position,
                        rendered_count=rendered_frame_count,
                        source_frame_index=source_index,
                        source_frame_count=source_frame_count,
                        video_time_seconds=float(frame_meta["video_time_seconds"]),
                        stride=stride,
                        segmentation_instances=int(segmentation["instance_count"]),
                        counts=counts,
                        confirmed_plants=totals["confirmed_plants"],
                        gap_candidates=totals["gap_candidates"],
                        gap_groups=totals["gap_groups"],
                        georeference_status=georeference_status,
                        telemetry=frame_meta,
                        heading=headings.get(srt_index),
                        plant_style=options.plant_style,
                    )
                    encoder.write(rendered)
                    frame_writer.writerow(
                        {
                            "rendered_frame_index": target_position,
                            "source_frame_index": source_index,
                            "video_time_seconds": frame_meta["video_time_seconds"],
                            "raw_mask_file": str(raw_path.relative_to(run_dir)),
                            "processed_mask_file": str(processed_path.relative_to(run_dir)),
                            "raw_mask_missing": raw_missing,
                            "processed_mask_missing": processed_missing,
                            "segmentation_instance_count": segmentation["instance_count"],
                            "safe_observations": counts["accepted"],
                            "boundary_review_observations": counts["boundary_review"],
                            "outside_rejected_observations": counts["outside_rejected"],
                            "low_confidence_observations": counts["low_confidence"],
                            "total_observations": len(observations),
                            "drone_latitude": frame_meta.get("drone_latitude", ""),
                            "drone_longitude": frame_meta.get("drone_longitude", ""),
                            "relative_altitude": frame_meta.get("relative_altitude", ""),
                            "absolute_altitude": frame_meta.get("absolute_altitude", ""),
                            "heading": (
                                headings[srt_index]
                                if headings.get(srt_index) is not None
                                else ""
                            ),
                            "gimbal_pitch": frame_meta.get("gimbal_pitch", ""),
                            "srt_sync_status": frame_meta.get("sync_status", ""),
                        }
                    )
                    rendered_totals["accepted"] += counts["accepted"]
                    rendered_totals["boundary_review"] += counts["boundary_review"]
                    rendered_totals["outside_rejected"] += counts["outside_rejected"]
                    rendered_totals["low_confidence"] += counts["low_confidence"]
                    rendered_totals["observations"] += len(observations)
                    target_position += 1
                    progress.update(
                        target_position,
                        safe=counts["accepted"],
                        boundary=counts["boundary_review"],
                        rejected=counts["outside_rejected"],
                    )
                    if target_position >= rendered_frame_count:
                        break
                    next_target = frame_indices[target_position]
                    del source_frame, rendered, raw_mask, processed_mask
                observation_stream.assert_exhausted()
            capture.release()
            capture = None
            if target_position != rendered_frame_count:
                raise OSError(
                    f"Rendered {target_position} frames; expected {rendered_frame_count}"
                )
            encoder.close()
            encoder = None

            if options.keep_audio and source_probe["audio_stream_count"] > 0:
                if not ffmpeg:
                    raise FileNotFoundError("FFmpeg is required for optional audio mux")
                announce("[Video Export] Optional audio mux")
                audio_status, audio_preserved = _mux_audio(
                    ffmpeg,
                    temporary_video,
                    source_video,
                    muxed_video,
                    log,
                )
                final_candidate = muxed_video
            elif source_probe["audio_stream_count"] == 0:
                audio_status = "source_has_no_audio"
            else:
                audio_status = "source_audio_not_requested"

            announce("[Video Export] Validating MP4")
            validation, validation_images = _validate_video(
                final_candidate,
                expected_size=output_size,
                expected_fps=output_fps,
                expected_frames=rendered_frame_count,
                source_duration=source_probe["duration_seconds"],
                faststart_required=faststart_required,
            )
            _write_contact_sheet(temporary_contact, validation_images)
            video_checksum = sha256_file(final_candidate)
            elapsed = time.perf_counter() - started
            manifest = {
                "status": "complete",
                "readiness": "demo_only",
                "created_at": _timestamp(),
                "source_run": str(run_dir),
                "source_run_manifest": str(run_manifest_path),
                "source_run_manifest_sha256": run_manifest_sha256,
                "source_video": str(source_video),
                "source_video_sha256": sha256_file(source_video),
                "source_frame_count": source_frame_count,
                "rendered_frame_count": rendered_frame_count,
                "source_fps": source_probe["fps"],
                "output_fps": output_fps,
                "frame_stride": stride,
                "frame_mapping": "exact cached processed source frame indices",
                "output_video": str(output_path),
                "output_resolution": {
                    "width": output_size[0],
                    "height": output_size[1],
                    "mode": options.resolution,
                },
                "encoder_requested": options.codec,
                "encoder_actual": selected_encoder,
                "ffmpeg_encoder": selected_encoder,
                "codec_actual": validation["codec"],
                "pixel_format": validation["pixel_format"],
                "crf": options.crf if selected_encoder == "libx264" else None,
                "preset": options.preset if selected_encoder == "libx264" else None,
                "requested_options": requested_options,
                "faststart": validation["faststart_detected"],
                "duration_seconds": validation["duration_seconds"],
                "file_size_bytes": final_candidate.stat().st_size,
                "audio": {
                    "requested": options.keep_audio,
                    "source_stream_count": source_probe["audio_stream_count"],
                    "source_codecs": source_probe["audio_codecs"],
                    "status": audio_status,
                    "preserved": audio_preserved,
                },
                "overlays": {
                    "processed_union_mask_closing_3_fill": True,
                    "processed_union_mask_outline": True,
                    "raw_union_mask_outline": True,
                    "safe_interior_boundary": True,
                    "boundary_review_corridor": True,
                    "plant_observations": options.plant_style,
                    "plant_coordinates": "exact cached source-frame center/bbox coordinates",
                    "low_confidence_threshold": LOW_CONFIDENCE_THRESHOLD,
                    "telemetry": True,
                    "telemetry_lat_lon_semantics": "Drone telemetry only",
                    "gap_run_counts": True,
                    "gap_geometry_projected_to_frame": False,
                    "gap_geometry_projection_reason": (
                        "Frame-to-local registration is estimated and inverse gap projection "
                        "has not been independently validated as exact"
                    ),
                },
                "observation_totals": rendered_totals,
                "missing_artifacts": {
                    "raw_mask_frames": missing_masks["raw"],
                    "processed_mask_frames": missing_masks["processed"],
                },
                "render_seconds": elapsed,
                "output_video_sha256": video_checksum,
                "validation": validation,
                "model_loading_performed": False,
                "detector_inference_performed": False,
                "segmenter_inference_performed": False,
                "deduplication_or_gap_analysis_recomputed": False,
                "skipped_frames_inferred": False,
                "limitations": [
                    (
                        f"Sampled inference review: {rendered_frame_count} / "
                        f"{source_frame_count} frames, stride {stride}."
                    ),
                    "No inference was performed on skipped frames.",
                    "Gap geometry is not projected into source frames; use gap_map_preview.png.",
                    "Drone telemetry latitude/longitude is not an object coordinate.",
                    "Segmentation-derived areas are not official or cadastral boundaries.",
                    "Candidate gaps require operator review; readiness remains demo_only.",
                ],
            }
            announce("[Video Export] Complete")

        os.replace(final_candidate, output_path)
        os.replace(temporary_frames, video_dir / FRAME_MANIFEST_NAME)
        os.replace(temporary_contact, video_dir / CONTACT_SHEET_NAME)
        os.replace(temporary_log, video_dir / VIDEO_LOG_NAME)
        manifest["file_size_bytes"] = output_path.stat().st_size
        write_json(video_dir / VIDEO_MANIFEST_NAME, manifest)
        return manifest
    except Exception:
        if encoder is not None:
            encoder.abort()
        if temporary_log.is_file():
            failed_log = video_dir / "video_export_failed.log"
            os.replace(temporary_log, failed_log)
        raise
    finally:
        if capture is not None:
            capture.release()
        for path in temporary_paths:
            if path.is_file():
                path.unlink()


def add_video_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_run_dir: bool,
    include_resume_force: bool,
) -> None:
    if include_run_dir:
        parser.add_argument("--run-dir", required=True, type=Path)
    if include_resume_force:
        parser.add_argument("--resume", action="store_true")
        parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--video-resolution",
        choices=["review", "source"],
        default="review",
    )
    parser.add_argument("--video-width", type=int, default=1920)
    parser.add_argument("--video-codec", default="auto")
    parser.add_argument("--video-crf", type=int, default=20)
    parser.add_argument("--video-preset", default="medium")
    parser.add_argument("--video-mask-alpha", type=float, default=0.25)
    parser.add_argument(
        "--video-plant-style",
        choices=["points", "boxes", "none"],
        default="points",
    )
    parser.add_argument("--video-keep-audio", action="store_true")
    parser.add_argument("--video-output", type=Path)


def options_from_args(args: argparse.Namespace) -> VideoExportOptions:
    return VideoExportOptions(
        resolution=args.video_resolution,
        width=args.video_width,
        codec=args.video_codec,
        crf=args.video_crf,
        preset=args.video_preset,
        mask_alpha=args.video_mask_alpha,
        plant_style=args.video_plant_style,
        keep_audio=args.video_keep_audio,
        output=args.video_output,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Render a sampled, offline annotated MP4 from completed cached inference artifacts. "
            "This module never loads detector or segmenter checkpoints."
        )
    )
    add_video_arguments(parser, include_run_dir=True, include_resume_force=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = export_review_video(
            args.run_dir,
            options_from_args(args),
            resume=args.resume,
            force=args.force,
        )
        print(manifest["output_video"])
        return 0
    except Exception as exc:
        print(f"[Video Export] Failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
