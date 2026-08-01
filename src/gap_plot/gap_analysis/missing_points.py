"""Missing planting-point detection contract."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from gap_plot.data.schemas import ExpectedPlantingPoint, MissingPlantingPoint, PlantDetection
from gap_plot.segmentation.interfaces import AreaSegmentationResult


@runtime_checkable
class MissingPointDetector(Protocol):
    """Contract for identifying expected points without nearby plants."""

    def detect(
        self,
        expected_points: Sequence[ExpectedPlantingPoint],
        plants: Sequence[PlantDetection],
        area_result: AreaSegmentationResult | None = None,
    ) -> Sequence[MissingPlantingPoint]:
        """Return expected points considered missing."""
