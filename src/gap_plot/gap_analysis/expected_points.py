"""Expected planting-point generation contract."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from gap_plot.data.schemas import ExpectedPlantingPoint
from gap_plot.geometry.rows import RowReconstructionResult
from gap_plot.segmentation.interfaces import AreaSegmentationResult


@runtime_checkable
class ExpectedPointGenerator(Protocol):
    """Contract for generating expected planting points from row patterns."""

    def generate(
        self,
        row_result: RowReconstructionResult,
        area_result: AreaSegmentationResult | None = None,
    ) -> Sequence[ExpectedPlantingPoint]:
        """Generate expected planting points inside valid planting areas."""
