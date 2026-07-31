from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Union


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
    aircraft_latitude: Optional[float] = None
    aircraft_longitude: Optional[float] = None
    relative_altitude: Optional[float] = None
    absolute_altitude: Optional[float] = None
    gimbal_pitch: Optional[float] = None
    aircraft_heading: Optional[float] = None
    rtk_status: Optional[Union[int, str]] = None
    source_timestamp: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return json_safe(asdict(self))


@dataclass
class ModelOutput:
    detections: List[Dict[str, Any]] = field(default_factory=list)
    contours: List[List[List[float]]] = field(default_factory=list)
    mask: Optional[Any] = None
    latency_ms: float = 0.0
    preprocessing_ms: Optional[float] = None
    inference_ms: Optional[float] = None
    postprocessing_ms: Optional[float] = None
    warning: Optional[str] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FrameResult:
    session_id: str
    frame_index: int
    capture_timestamp: str
    inference_timestamp: str
    camera_source: str
    image_width: int
    image_height: int
    plant_detections: List[Dict[str, Any]]
    plot_segmentation: Optional[Dict[str, Any]]
    model_version: Dict[str, str]
    model_sha256: Dict[str, str]
    confidence_threshold: Dict[str, float]
    inference_latency_ms: float
    aircraft_latitude: Optional[float]
    aircraft_longitude: Optional[float]
    relative_altitude: Optional[float]
    absolute_altitude: Optional[float]
    gimbal_pitch: Optional[float]
    aircraft_heading: Optional[float]
    rtk_status: Optional[Union[int, str]]
    warning: List[str]
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return json_safe(asdict(self))
