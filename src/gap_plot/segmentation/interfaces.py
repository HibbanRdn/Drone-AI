"""Interfaces for plantable-area segmentation adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable


AREA_CLASSES = (
    "planting_area",
    "road",
    "ditch_or_drainage",
    "block_boundary",
    "non_planting_area",
)


@dataclass(frozen=True)
class AreaSegmentationResult:
    """Segmentation result boundary for area masks or polygons."""

    frame_id: str
    mask_path: str | None = None
    polygons: Mapping[str, object] = field(default_factory=dict)
    class_confidences: Mapping[str, float] = field(default_factory=dict)
    model_version: str = "unknown"


@runtime_checkable
class AreaSegmenter(Protocol):
    """Contract for plantable-area segmentation models."""

    @property
    def model_version(self) -> str:
        """Return model version or `unknown`."""

    def segment(self, image: Any, frame_id: str) -> AreaSegmentationResult:
        """Segment plantable and non-plantable areas."""
