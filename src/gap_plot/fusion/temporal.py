"""Temporal gap fusion contract."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from gap_plot.data.schemas import GapResult


@runtime_checkable
class TemporalFusion(Protocol):
    """Contract for deduplicating gaps seen across frames."""

    def fuse(self, gaps: Sequence[GapResult]) -> Sequence[GapResult]:
        """Fuse repeated observations of the same physical gap."""
