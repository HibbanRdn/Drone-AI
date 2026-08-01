from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass

from .srt_parser import SRTEntry


@dataclass(frozen=True)
class SyncResult:
    frame_index: int
    video_time_seconds: float
    srt_index: int | None
    srt_start_seconds: float | None
    srt_end_seconds: float | None
    sync_delta_seconds: float | None
    sync_status: str
    telemetry: SRTEntry | None

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["telemetry"] = self.telemetry.to_dict() if self.telemetry else None
        return value


class TelemetrySynchronizer:
    def __init__(
        self,
        entries: list[SRTEntry],
        tolerance_seconds: float = 0.2,
        time_offset_seconds: float = 0.0,
    ):
        self.entries = sorted(entries, key=lambda entry: entry.start_seconds)
        self.starts = [entry.start_seconds for entry in self.entries]
        self.tolerance_seconds = float(tolerance_seconds)
        self.time_offset_seconds = float(time_offset_seconds)

    def sync(self, frame_index: int, fps: float) -> SyncResult:
        if fps <= 0:
            raise ValueError("FPS harus positif untuk sinkronisasi.")
        video_time = frame_index / fps
        query_time = video_time + self.time_offset_seconds
        if not self.entries:
            return SyncResult(frame_index, video_time, None, None, None, None, "missing", None)

        position = bisect_right(self.starts, query_time) - 1
        if position >= 0:
            candidate = self.entries[position]
            if candidate.start_seconds <= query_time < candidate.end_seconds:
                return self._result(frame_index, video_time, query_time, candidate, "exact_interval")

        neighbor_positions = {max(0, position), min(len(self.entries) - 1, position + 1)}
        best: tuple[float, SRTEntry] | None = None
        for neighbor in neighbor_positions:
            entry = self.entries[neighbor]
            if query_time < entry.start_seconds:
                gap = entry.start_seconds - query_time
            elif query_time >= entry.end_seconds:
                gap = query_time - entry.end_seconds
            else:
                gap = 0.0
            if best is None or gap < best[0]:
                best = (gap, entry)
        if best and best[0] <= self.tolerance_seconds:
            return self._result(
                frame_index, video_time, query_time, best[1], "nearest_within_tolerance"
            )
        return SyncResult(frame_index, video_time, None, None, None, None, "missing", None)

    @staticmethod
    def _result(
        frame_index: int,
        video_time: float,
        query_time: float,
        entry: SRTEntry,
        status: str,
    ) -> SyncResult:
        return SyncResult(
            frame_index=frame_index,
            video_time_seconds=video_time,
            srt_index=entry.index,
            srt_start_seconds=entry.start_seconds,
            srt_end_seconds=entry.end_seconds,
            sync_delta_seconds=query_time - entry.start_seconds,
            sync_status=status,
            telemetry=entry,
        )
