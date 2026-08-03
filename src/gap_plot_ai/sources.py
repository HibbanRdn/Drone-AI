from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Protocol, Union

import cv2
import numpy as np

from .schema import Telemetry


@dataclass(frozen=True)
class FrameMetadata:
    sequence: int
    source_frame_id: int
    capture_monotonic_ns: int
    capture_wall_clock_ns: int
    width: int
    height: int
    row_stride: int
    pixel_format: str
    camera_source: str
    source_fps: Optional[float] = None

    @property
    def capture_timestamp(self) -> str:
        return datetime.fromtimestamp(
            self.capture_wall_clock_ns / 1_000_000_000, timezone.utc
        ).isoformat()


@dataclass
class FrameEnvelope:
    frame_bgr: np.ndarray
    metadata: FrameMetadata
    telemetry: Telemetry


class FrameSource(Protocol):
    def frames(self) -> Iterator[FrameEnvelope]:
        ...

    def close(self) -> None:
        ...


class ReplayFrameSource:
    """MP4 source for orchestration tests; MP4 telemetry is intentionally ignored."""

    def __init__(
        self,
        video_path: Union[str, Path],
        *,
        realtime: bool = False,
        max_frames: Optional[int] = None,
    ) -> None:
        self.video_path = Path(video_path).expanduser().resolve()
        if not self.video_path.is_file():
            raise FileNotFoundError(self.video_path)
        self.realtime = realtime
        self.max_frames = max_frames
        self._capture: Optional[cv2.VideoCapture] = None
        self._closed = False

    def frames(self) -> Iterator[FrameEnvelope]:
        capture = cv2.VideoCapture(str(self.video_path))
        self._capture = capture
        if not capture.isOpened():
            raise OSError(f"Replay video tidak dapat dibuka: {self.video_path}")
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        interval = 1.0 / fps if fps > 0 else 0.0
        sequence = 0
        next_deadline = time.monotonic()
        while not self._closed:
            if self.max_frames is not None and sequence >= self.max_frames:
                break
            ok, frame = capture.read()
            if not ok:
                break
            if self.realtime and interval > 0:
                remaining = next_deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                next_deadline = max(next_deadline + interval, time.monotonic())
            wall_ns = time.time_ns()
            height, width = frame.shape[:2]
            yield FrameEnvelope(
                frame_bgr=frame,
                metadata=FrameMetadata(
                    sequence=sequence,
                    source_frame_id=sequence,
                    capture_monotonic_ns=time.monotonic_ns(),
                    capture_wall_clock_ns=wall_ns,
                    width=width,
                    height=height,
                    row_stride=int(frame.strides[0]),
                    pixel_format="BGR_PACKED_REPLAY",
                    camera_source="replay_mp4",
                    source_fps=fps or None,
                ),
                telemetry=Telemetry(
                    source_timestamp=datetime.fromtimestamp(
                        wall_ns / 1_000_000_000, timezone.utc
                    ).isoformat()
                ),
            )
            sequence += 1
        capture.release()
        self._capture = None

    def close(self) -> None:
        self._closed = True
        if self._capture is not None:
            self._capture.release()


class ArrayFrameSource:
    """Deterministic fixture source used by replay integration tests."""

    def __init__(self, frames: List[np.ndarray], *, source_fps: float = 30.0):
        self._frames = frames
        self._source_fps = source_fps
        self._closed = False

    def frames(self) -> Iterator[FrameEnvelope]:
        for sequence, frame in enumerate(self._frames):
            if self._closed:
                break
            wall_ns = time.time_ns()
            height, width = frame.shape[:2]
            yield FrameEnvelope(
                frame_bgr=frame,
                metadata=FrameMetadata(
                    sequence=sequence,
                    source_frame_id=sequence,
                    capture_monotonic_ns=time.monotonic_ns(),
                    capture_wall_clock_ns=wall_ns,
                    width=width,
                    height=height,
                    row_stride=int(frame.strides[0]),
                    pixel_format="BGR_PACKED_FIXTURE",
                    camera_source="fixture",
                    source_fps=self._source_fps,
                ),
                telemetry=Telemetry(),
            )

    def close(self) -> None:
        self._closed = True
