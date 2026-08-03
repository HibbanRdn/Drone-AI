from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Union

import numpy as np
import cv2

from .schema import ModelOutput


@dataclass(frozen=True)
class TileWindow:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class TiledDetection:
    class_id: int
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    source_tile_x: int
    source_tile_y: int
    source_tile_width: int
    source_tile_height: int

    @property
    def center_x(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def center_y(self) -> float:
        return (self.y1 + self.y2) / 2.0

    def to_runtime_dict(self) -> Dict[str, Any]:
        return {
            "bbox_xyxy": [self.x1, self.y1, self.x2, self.y2],
            "confidence": self.confidence,
            "class_id": self.class_id,
            "class_name": self.class_name,
            "center_xy": [self.center_x, self.center_y],
            "source_tile": {
                "x": self.source_tile_x,
                "y": self.source_tile_y,
                "width": self.source_tile_width,
                "height": self.source_tile_height,
            },
        }


def _axis_positions(
    origin: int, length: int, tile_size: int, overlap: int
) -> List[int]:
    if length <= tile_size:
        return [origin]
    stride = tile_size - overlap
    last = origin + length - tile_size
    positions = list(range(origin, last + 1, stride))
    if positions[-1] != last:
        positions.append(last)
    return positions


def generate_tile_windows(
    frame_width: int,
    frame_height: int,
    tile_size: int,
    overlap: int,
) -> List[TileWindow]:
    if frame_width <= 0 or frame_height <= 0 or tile_size <= 0:
        raise ValueError("frame dimensions and tile_size must be positive")
    if overlap < 0 or overlap >= tile_size:
        raise ValueError("overlap must be in [0, tile_size)")
    xs = _axis_positions(0, frame_width, tile_size, overlap)
    ys = _axis_positions(0, frame_height, tile_size, overlap)
    return [
        TileWindow(
            x=x,
            y=y,
            width=min(tile_size, frame_width - x),
            height=min(tile_size, frame_height - y),
        )
        for y in ys
        for x in xs
    ]


def local_box_to_global(
    box: Union[List[float], Tuple[float, float, float, float]],
    tile: TileWindow,
) -> Tuple[float, float, float, float]:
    x1, y1, x2, y2 = box
    return x1 + tile.x, y1 + tile.y, x2 + tile.x, y2 + tile.y


def box_iou(a: TiledDetection, b: TiledDetection) -> float:
    intersection_width = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1))
    intersection_height = max(0.0, min(a.y2, b.y2) - max(a.y1, b.y1))
    intersection = intersection_width * intersection_height
    area_a = max(0.0, a.x2 - a.x1) * max(0.0, a.y2 - a.y1)
    area_b = max(0.0, b.x2 - b.x1) * max(0.0, b.y2 - b.y1)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def class_aware_nms(
    detections: List[TiledDetection],
    iou_threshold: float,
    backend: str = "torchvision",
) -> List[TiledDetection]:
    if not detections:
        return []
    if backend == "opencv":
        kept: List[TiledDetection] = []
        class_ids = sorted(set(item.class_id for item in detections))
        for class_id in class_ids:
            candidates = [item for item in detections if item.class_id == class_id]
            indexes = cv2.dnn.NMSBoxes(
                [
                    [
                        item.x1,
                        item.y1,
                        item.x2 - item.x1,
                        item.y2 - item.y1,
                    ]
                    for item in candidates
                ],
                [item.confidence for item in candidates],
                score_threshold=0.0,
                nms_threshold=float(iou_threshold),
            )
            if indexes is not None:
                kept.extend(
                    candidates[int(index)]
                    for index in np.asarray(indexes).reshape(-1).tolist()
                )
        return sorted(kept, key=lambda item: item.confidence, reverse=True)
    if backend != "torchvision":
        raise ValueError("global NMS backend must be torchvision or opencv")
    try:
        import torch
        from torchvision.ops import batched_nms
    except ImportError as exc:
        raise RuntimeError(
            "Torch/TorchVision wajib tersedia untuk global NMS tervalidasi."
        ) from exc
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    boxes = torch.tensor(
        [[item.x1, item.y1, item.x2, item.y2] for item in detections],
        dtype=torch.float32,
        device=device,
    )
    scores = torch.tensor(
        [item.confidence for item in detections], dtype=torch.float32, device=device
    )
    class_ids = torch.tensor(
        [item.class_id for item in detections], dtype=torch.int64, device=device
    )
    keep = batched_nms(boxes, scores, class_ids, float(iou_threshold))
    indices = keep.detach().cpu().tolist()
    return [detections[int(index)] for index in indices]


def center_distance_suppression(
    detections: List[TiledDetection], radius_px: float
) -> List[TiledDetection]:
    if radius_px <= 0:
        return list(detections)
    kept: List[TiledDetection] = []
    radius_squared = radius_px * radius_px
    for candidate in sorted(
        detections, key=lambda item: item.confidence, reverse=True
    ):
        duplicate = False
        for selected in kept:
            if selected.class_id != candidate.class_id:
                continue
            distance_squared = (
                (selected.center_x - candidate.center_x) ** 2
                + (selected.center_y - candidate.center_y) ** 2
            )
            if distance_squared <= radius_squared:
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
    return kept


def merge_detections(
    detections: List[TiledDetection],
    *,
    global_nms_iou: float,
    center_duplicate_radius_px: float,
    enable_center_suppression: bool,
    max_detections_full_frame: int,
    global_nms_backend: str = "torchvision",
) -> Tuple[List[TiledDetection], Dict[str, Any]]:
    nms_started = time.perf_counter()
    after_nms = class_aware_nms(
        detections, global_nms_iou, backend=global_nms_backend
    )
    global_nms_ms = (time.perf_counter() - nms_started) * 1000
    center_started = time.perf_counter()
    after_center = (
        center_distance_suppression(after_nms, center_duplicate_radius_px)
        if enable_center_suppression
        else after_nms
    )
    center_suppression_ms = (time.perf_counter() - center_started) * 1000
    final = sorted(
        after_center, key=lambda item: item.confidence, reverse=True
    )[:max_detections_full_frame]
    return final, {
        "global_nms_backend": (
            "torchvision_cuda"
            if global_nms_backend == "torchvision" and _torchvision_nms_uses_cuda()
            else "torchvision_cpu"
            if global_nms_backend == "torchvision"
            else "opencv_native"
        ),
        "global_nms_ms": global_nms_ms,
        "center_suppression_ms": center_suppression_ms,
        "raw_tile_predictions": len(detections),
        "after_global_nms": len(after_nms),
        "after_center_suppression": len(final),
        "duplicates_removed": len(detections) - len(final),
        "center_suppression_enabled": bool(enable_center_suppression),
    }


def _torchvision_nms_uses_cuda() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def predict_tiled_detector(
    model: Any, frame_bgr: np.ndarray, detector_config: Dict[str, Any]
) -> ModelOutput:
    height, width = frame_bgr.shape[:2]
    windows = generate_tile_windows(
        width,
        height,
        int(detector_config["tile_size"]),
        int(detector_config["tile_overlap"]),
    )
    started = time.perf_counter()
    raw: List[TiledDetection] = []
    warnings: List[str] = []
    preprocessing_ms = 0.0
    inference_ms = 0.0
    postprocessing_ms = 0.0
    for window in windows:
        tile = frame_bgr[
            window.y : window.y + window.height,
            window.x : window.x + window.width,
        ]
        output = model.predict(tile)
        if output.warning:
            warnings.append(
                f"tile({window.x},{window.y},{window.width},{window.height}):"
                f"{output.warning}"
            )
            continue
        preprocessing_ms += float(output.preprocessing_ms or 0)
        inference_ms += float(output.inference_ms or 0)
        postprocessing_ms += float(output.postprocessing_ms or 0)
        for detection in output.detections:
            box = detection.get("bbox_xyxy", [])
            if len(box) != 4:
                continue
            x1, y1, x2, y2 = local_box_to_global(box, window)
            raw.append(
                TiledDetection(
                    class_id=int(detection.get("class_id", 0)),
                    class_name=str(detection.get("class_name", "unknown")),
                    confidence=float(detection.get("confidence", 0)),
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    source_tile_x=window.x,
                    source_tile_y=window.y,
                    source_tile_width=window.width,
                    source_tile_height=window.height,
                )
            )
    merged, diagnostics = merge_detections(
        raw,
        global_nms_iou=float(detector_config["global_nms_iou"]),
        center_duplicate_radius_px=float(
            detector_config["center_duplicate_radius_px"]
        ),
        enable_center_suppression=bool(
            detector_config["enable_center_suppression"]
        ),
        max_detections_full_frame=int(
            detector_config["max_detections_full_frame"]
        ),
        global_nms_backend=str(detector_config["global_nms_backend"]),
    )
    diagnostics["tile_count"] = len(windows)
    diagnostics["failed_tile_count"] = len(warnings)
    return ModelOutput(
        detections=[detection.to_runtime_dict() for detection in merged],
        latency_ms=(time.perf_counter() - started) * 1000,
        preprocessing_ms=preprocessing_ms,
        inference_ms=inference_ms,
        postprocessing_ms=postprocessing_ms,
        warning="; ".join(warnings) if warnings else None,
        diagnostics=diagnostics,
    )
