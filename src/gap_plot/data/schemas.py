"""Core data contracts shared across the pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class PixelPoint:
    """Point in image pixel coordinates."""

    x: float
    y: float


@dataclass(frozen=True)
class GeoPoint:
    """Point in geographic coordinates."""

    longitude: float
    latitude: float
    altitude_m: float | None = None


@dataclass(frozen=True)
class BoundingBox:
    """Axis-aligned image bounding box in pixel coordinates."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float

    def width(self) -> float:
        """Return box width in pixels."""

        return self.x_max - self.x_min

    def height(self) -> float:
        """Return box height in pixels."""

        return self.y_max - self.y_min


@dataclass(frozen=True)
class FrameMetadata:
    """Metadata for one extracted or selected frame."""

    frame_id: str
    source_video: str = "unknown"
    timestamp_seconds: float | None = None
    flight_id: str = "unknown"
    block: str = "unknown"
    plot: str = "unknown"
    crop_cycle: str = "unknown"
    growth_stage: str = "unknown"
    altitude: str | float = "unknown"
    camera_angle: str = "unknown"
    gimbal_pitch: str | float = "unknown"
    focal_setting: str | float = "unknown"
    resolution_width: int | None = None
    resolution_height: int | None = None
    date: str = "unknown"
    lighting_condition: str = "unknown"
    annotation_status: str = "unannotated"
    notes: str = "unknown"


@dataclass(frozen=True)
class PlantDetection:
    """Detected pineapple center in full-frame pixel coordinates."""

    plant_id: str
    frame_id: str
    tile_id: str
    center: PixelPoint
    confidence: float
    plant_condition: str = "unknown"
    bbox: BoundingBox | None = None
    model_version: str = "unknown"


@dataclass(frozen=True)
class ExpectedPlantingPoint:
    """A planting point inferred from the reconstructed row pattern."""

    point_id: str
    frame_id: str
    row_id: str
    slot_index: int
    center: PixelPoint
    row_confidence: float
    spacing_confidence: float
    boundary_distance_px: float | None = None


@dataclass(frozen=True)
class MissingPlantingPoint:
    """Expected planting point without a nearby plant detection."""

    missing_id: str
    expected_point_id: str
    frame_id: str
    center: PixelPoint
    confidence: float
    reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class GapResult:
    """Final gap candidate or fused gap result."""

    gap_id: str
    frame_ids: tuple[str, ...]
    polygon_pixel: tuple[PixelPoint, ...]
    polygon_geo: tuple[GeoPoint, ...] | None
    center_pixel: PixelPoint
    center_geo: GeoPoint | None
    estimated_missing_plants: int
    visible_bare_gap_area_px2: float | None
    visible_bare_gap_area_m2: float | None
    effective_lost_area_m2: float | None
    confidence: float
    confidence_level: str
    distance_to_boundary_px: float | None
    model_versions: Mapping[str, str] = field(default_factory=dict)
    missing_point_ids: tuple[str, ...] = field(default_factory=tuple)
    evidence_frame_ids: tuple[str, ...] = field(default_factory=tuple)


def to_plain_data(value: Any) -> Any:
    """Convert dataclasses and nested containers into JSON-compatible data."""

    if is_dataclass(value):
        return to_plain_data(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): to_plain_data(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [to_plain_data(item) for item in value]
    return value
