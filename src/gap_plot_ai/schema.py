from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any


def json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


@dataclass
class Telemetry:
    aircraft_latitude: float | None = None
    aircraft_longitude: float | None = None
    relative_altitude: float | None = None
    absolute_altitude: float | None = None
    gimbal_pitch: float | None = None
    aircraft_heading: float | None = None
    rtk_status: int | str | None = None
    source_timestamp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return json_safe(asdict(self))


@dataclass
class ModelOutput:
    detections: list[dict[str, Any]] = field(default_factory=list)
    contours: list[list[list[float]]] = field(default_factory=list)
    mask: Any | None = None
    latency_ms: float = 0.0
    preprocessing_ms: float | None = None
    inference_ms: float | None = None
    postprocessing_ms: float | None = None
    warning: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class FrameResult:
    session_id: str
    frame_index: int
    capture_timestamp: str
    inference_timestamp: str
    camera_source: str
    image_width: int
    image_height: int
    plant_detections: list[dict[str, Any]]
    plot_segmentation: dict[str, Any] | None
    model_version: dict[str, str]
    model_sha256: dict[str, str]
    confidence_threshold: dict[str, float]
    inference_latency_ms: float
    aircraft_latitude: float | None
    aircraft_longitude: float | None
    relative_altitude: float | None
    absolute_altitude: float | None
    gimbal_pitch: float | None
    aircraft_heading: float | None
    rtk_status: int | str | None
    warning: list[str]
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return json_safe(asdict(self))
