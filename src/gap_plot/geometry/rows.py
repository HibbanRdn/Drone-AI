"""Plant row reconstruction contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable

from gap_plot.data.schemas import PixelPoint, PlantDetection


@dataclass(frozen=True)
class ReconstructedRow:
    """One reconstructed planting row."""

    row_id: str
    plant_ids: tuple[str, ...]
    centerline: tuple[PixelPoint, ...]
    median_spacing_px: float | None
    confidence: float
    method: str


@dataclass(frozen=True)
class RowReconstructionResult:
    """Output of a row reconstruction component."""

    frame_id: str
    rows: tuple[ReconstructedRow, ...]
    dominant_orientation_deg: float | None
    inter_row_spacing_px: float | None
    diagnostics: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class RowReconstructor(Protocol):
    """Contract for row reconstruction algorithms."""

    def reconstruct(self, frame_id: str, plants: Sequence[PlantDetection]) -> RowReconstructionResult:
        """Reconstruct planting rows from plant centers."""
