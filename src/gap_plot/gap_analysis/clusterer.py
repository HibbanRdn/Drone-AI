"""Gap grouping contract."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from gap_plot.data.schemas import GapResult, MissingPlantingPoint


@runtime_checkable
class GapClusterer(Protocol):
    """Contract for grouping nearby missing points into gap polygons."""

    def cluster(self, missing_points: Sequence[MissingPlantingPoint]) -> Sequence[GapResult]:
        """Group missing planting points into gap candidates."""
