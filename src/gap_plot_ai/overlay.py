from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Tuple, Union


PSDK_BOX_COUNT_LIMIT = 255


@dataclass(frozen=True)
class OverlaySelection:
    detections: List[Dict[str, Any]]
    total_detections: int
    eligible_detections: int
    sent_objects: int
    truncated_objects: int
    api_limit: int
    strategy: str


class CoordinateMapper:
    """Map source pixel boxes into the normalized coordinate space used by PSDK."""

    def __init__(
        self,
        source_width: int,
        source_height: int,
        *,
        target_width: int = 10000,
        target_height: int = 10000,
        aspect_mode: str = "stretch",
        rotation_degrees: int = 0,
    ) -> None:
        if min(source_width, source_height, target_width, target_height) <= 0:
            raise ValueError("coordinate dimensions harus positif")
        if aspect_mode not in {"stretch", "contain", "cover"}:
            raise ValueError("aspect_mode harus stretch/contain/cover")
        if rotation_degrees not in {0, 90, 180, 270}:
            raise ValueError("rotation_degrees harus 0/90/180/270")
        self.source_width = source_width
        self.source_height = source_height
        self.target_width = target_width
        self.target_height = target_height
        self.aspect_mode = aspect_mode
        self.rotation_degrees = rotation_degrees

    def _map_point(self, x: float, y: float) -> Tuple[float, float]:
        x = min(max(float(x), 0.0), float(self.source_width))
        y = min(max(float(y), 0.0), float(self.source_height))
        if self.aspect_mode == "stretch":
            tx = x / self.source_width * self.target_width
            ty = y / self.source_height * self.target_height
        else:
            scale_x = self.target_width / self.source_width
            scale_y = self.target_height / self.source_height
            scale = min(scale_x, scale_y) if self.aspect_mode == "contain" else max(scale_x, scale_y)
            rendered_width = self.source_width * scale
            rendered_height = self.source_height * scale
            tx = x * scale + (self.target_width - rendered_width) / 2.0
            ty = y * scale + (self.target_height - rendered_height) / 2.0
        nx = tx / self.target_width
        ny = ty / self.target_height
        if self.rotation_degrees == 90:
            nx, ny = 1.0 - ny, nx
        elif self.rotation_degrees == 180:
            nx, ny = 1.0 - nx, 1.0 - ny
        elif self.rotation_degrees == 270:
            nx, ny = ny, 1.0 - nx
        return (
            min(max(nx, 0.0), 1.0) * self.target_width,
            min(max(ny, 0.0), 1.0) * self.target_height,
        )

    def map_box(self, box_xyxy: Sequence[float]) -> Tuple[int, int, int, int]:
        if len(box_xyxy) != 4:
            raise ValueError("bbox harus xyxy dengan empat nilai")
        x1, y1, x2, y2 = [float(value) for value in box_xyxy]
        if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
            raise ValueError("bbox mengandung nilai non-finite")
        points = [
            self._map_point(x1, y1),
            self._map_point(x2, y1),
            self._map_point(x1, y2),
            self._map_point(x2, y2),
        ]
        left = int(round(min(point[0] for point in points)))
        top = int(round(min(point[1] for point in points)))
        right = int(round(max(point[0] for point in points)))
        bottom = int(round(max(point[1] for point in points)))
        left = min(max(left, 0), self.target_width)
        top = min(max(top, 0), self.target_height)
        right = min(max(right, 0), self.target_width)
        bottom = min(max(bottom, 0), self.target_height)
        return left, top, right, bottom


def resolve_max_objects(value: Union[str, int], api_limit: int = PSDK_BOX_COUNT_LIMIT) -> int:
    if value == "auto":
        return api_limit
    result = int(value)
    if not 1 <= result <= api_limit:
        raise ValueError(f"max_objects harus 1..{api_limit} atau auto")
    return result


def select_overlay_detections(
    detections: Iterable[Dict[str, Any]],
    *,
    max_objects: Union[str, int],
    min_confidence: float,
    frame_width: int,
    frame_height: int,
    strategy: str = "confidence",
) -> OverlaySelection:
    if strategy not in {"confidence", "confidence_central"}:
        raise ValueError("overlay selection strategy tidak didukung")
    all_detections = list(detections)
    eligible = []
    center_x = frame_width / 2.0
    center_y = frame_height / 2.0
    diagonal = max(math.hypot(frame_width, frame_height), 1.0)
    for item in all_detections:
        box = item.get("bbox_xyxy", [])
        confidence = float(item.get("confidence", 0.0))
        if len(box) != 4 or confidence < min_confidence:
            continue
        try:
            values = [float(value) for value in box]
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in values):
            continue
        bx = (values[0] + values[2]) / 2.0
        by = (values[1] + values[3]) / 2.0
        centrality = 1.0 - min(math.hypot(bx - center_x, by - center_y) / diagonal, 1.0)
        score = confidence if strategy == "confidence" else confidence * 0.9 + centrality * 0.1
        eligible.append((score, confidence, item))
    eligible.sort(key=lambda value: (value[0], value[1]), reverse=True)
    limit = resolve_max_objects(max_objects)
    selected = [item for _, _, item in eligible[:limit]]
    return OverlaySelection(
        detections=selected,
        total_detections=len(all_detections),
        eligible_detections=len(eligible),
        sent_objects=len(selected),
        truncated_objects=max(0, len(eligible) - len(selected)),
        api_limit=PSDK_BOX_COUNT_LIMIT,
        strategy=strategy,
    )


def static_debug_boxes(width: int, height: int) -> List[Dict[str, Any]]:
    box_width = max(width * 0.08, 2.0)
    box_height = max(height * 0.08, 2.0)
    centers = [
        (width * 0.5, height * 0.5, "center"),
        (width * 0.06, height * 0.06, "top_left"),
        (width * 0.94, height * 0.06, "top_right"),
        (width * 0.06, height * 0.94, "bottom_left"),
        (width * 0.94, height * 0.94, "bottom_right"),
    ]
    result = []
    for cx, cy, label in centers:
        result.append(
            {
                "bbox_xyxy": [
                    max(0.0, cx - box_width / 2),
                    max(0.0, cy - box_height / 2),
                    min(float(width), cx + box_width / 2),
                    min(float(height), cy + box_height / 2),
                ],
                "confidence": 1.0,
                "class_id": 0,
                "class_name": f"debug_{label}",
            }
        )
    return result
