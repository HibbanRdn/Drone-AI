from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np


@dataclass(frozen=True)
class VideoMetadata:
    path: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration_seconds: float
    codec_fourcc: str
    first_frame_readable: bool
    orientation_degrees: int | None
    creation_time: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class VideoReader:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.capture = cv2.VideoCapture(str(self.path))
        if not self.capture.isOpened():
            raise OSError(f"Video tidak dapat dibuka: {self.path}")

    @property
    def width(self) -> int:
        return int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))

    @property
    def height(self) -> int:
        return int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))

    @property
    def fps(self) -> float:
        return float(self.capture.get(cv2.CAP_PROP_FPS))

    @property
    def frame_count(self) -> int:
        return int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))

    def read_frame(self, frame_index: int) -> np.ndarray:
        if frame_index < 0 or frame_index >= self.frame_count:
            raise IndexError(f"Frame {frame_index} di luar [0, {self.frame_count}).")
        self.capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = self.capture.read()
        if ok and frame is not None:
            return frame
        # H.264 backends occasionally fail when seeking directly to the final P/B frame.
        # Decode forward from a safe earlier point; if that seek also fails, decode from zero.
        for start_index in (max(0, frame_index - 120),):
            fallback = cv2.VideoCapture(str(self.path))
            if not fallback.isOpened():
                fallback.release()
                continue
            fallback.set(cv2.CAP_PROP_POS_FRAMES, start_index)
            recovered = None
            for _ in range(start_index, frame_index + 1):
                fallback_ok, candidate = fallback.read()
                if not fallback_ok or candidate is None:
                    recovered = None
                    break
                recovered = candidate
            if recovered is not None:
                self.capture.release()
                self.capture = fallback
                return recovered
            fallback.release()
        timestamp = frame_index / self.fps
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{timestamp:.9f}",
            "-i",
            str(self.path),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "bmp",
            "-",
        ]
        try:
            result = subprocess.run(command, check=True, capture_output=True, timeout=30)
            recovered = cv2.imdecode(np.frombuffer(result.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
            if recovered is not None:
                return recovered
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
        fallback = cv2.VideoCapture(str(self.path))
        if fallback.isOpened():
            recovered = None
            for _ in range(frame_index + 1):
                fallback_ok, candidate = fallback.read()
                if not fallback_ok or candidate is None:
                    recovered = None
                    break
                recovered = candidate
            if recovered is not None:
                self.capture.release()
                self.capture = fallback
                return recovered
        fallback.release()
        raise OSError(f"Gagal membaca frame {frame_index} dari {self.path}")

    def iter_frames(self) -> Iterator[tuple[int, np.ndarray]]:
        self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        index = 0
        while True:
            ok, frame = self.capture.read()
            if not ok or frame is None:
                break
            yield index, frame
            index += 1

    def close(self) -> None:
        self.capture.release()

    def __enter__(self) -> "VideoReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _ffprobe(path: Path) -> dict[str, object]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
        return {}


def audit_video(path: str | Path) -> dict[str, object]:
    video_path = Path(path).expanduser().resolve()
    probe = _ffprobe(video_path)
    video_streams = [
        stream
        for stream in probe.get("streams", [])
        if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic")
    ]
    primary = video_streams[0] if video_streams else {}
    format_info = probe.get("format", {})
    capture = cv2.VideoCapture(str(video_path))
    opened = capture.isOpened()
    ok, frame = capture.read() if opened else (False, None)
    width = int(primary.get("width") or capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(primary.get("height") or capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(primary.get("nb_frames") or capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = float(format_info.get("duration") or (frame_count / fps if fps else 0.0))
    codec_name = primary.get("codec_name")
    codec_long = primary.get("codec_long_name")
    tags = primary.get("tags", {})
    rotation = tags.get("rotate")
    capture.release()
    metadata = VideoMetadata(
        path=str(video_path),
        width=width,
        height=height,
        fps=fps,
        frame_count=frame_count,
        duration_seconds=duration,
        codec_fourcc=str(codec_name or "unknown"),
        first_frame_readable=bool(ok and frame is not None),
        orientation_degrees=int(rotation) if rotation is not None else None,
        creation_time=tags.get("creation_time") or format_info.get("tags", {}).get("creation_time"),
    )
    result = metadata.to_dict()
    result.update(
        {
            "codec_long_name": codec_long,
            "container_format": format_info.get("format_long_name"),
            "pixel_format": primary.get("pix_fmt"),
            "file_size_bytes": video_path.stat().st_size,
            "probe_backend": "ffprobe" if probe else "opencv",
            "frame_shape_first": list(frame.shape) if frame is not None else None,
        }
    )
    return result
