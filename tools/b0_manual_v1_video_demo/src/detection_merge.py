from __future__ import annotations

from dataclasses import dataclass

from .tiled_inference import Detection


@dataclass(frozen=True)
class MergeDiagnostics:
    raw_tile_predictions: int
    after_global_nms: int
    after_center_suppression: int
    duplicates_removed: int

    def to_dict(self) -> dict[str, int]:
        return {
            "raw_tile_predictions": self.raw_tile_predictions,
            "after_global_nms": self.after_global_nms,
            "after_center_suppression": self.after_center_suppression,
            "duplicates_removed": self.duplicates_removed,
        }


def box_iou(a: Detection, b: Detection) -> float:
    intersection_width = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1))
    intersection_height = max(0.0, min(a.y2, b.y2) - max(a.y1, b.y1))
    intersection = intersection_width * intersection_height
    area_a = max(0.0, a.x2 - a.x1) * max(0.0, a.y2 - a.y1)
    area_b = max(0.0, b.x2 - b.x1) * max(0.0, b.y2 - b.y1)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def class_aware_nms(detections: list[Detection], iou_threshold: float) -> list[Detection]:
    if not detections:
        return []
    try:
        import torch
        from torchvision.ops import batched_nms
    except ImportError as exc:
        raise RuntimeError("Torch/TorchVision wajib tersedia untuk global NMS.") from exc
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    boxes = torch.tensor(
        [[item.x1, item.y1, item.x2, item.y2] for item in detections],
        dtype=torch.float32,
        device=device,
    )
    scores = torch.tensor(
        [item.confidence for item in detections], dtype=torch.float32, device=device
    )
    classes = torch.tensor(
        [item.class_id for item in detections], dtype=torch.int64, device=device
    )
    indices = batched_nms(boxes, scores, classes, float(iou_threshold))
    return [detections[int(index)] for index in indices.detach().cpu().tolist()]


def center_distance_suppression(
    detections: list[Detection], radius_px: float
) -> list[Detection]:
    if radius_px <= 0:
        return list(detections)
    kept: list[Detection] = []
    radius_squared = radius_px * radius_px
    for candidate in sorted(detections, key=lambda item: item.confidence, reverse=True):
        duplicate = False
        for selected in kept:
            if selected.class_id != candidate.class_id:
                continue
            distance_squared = (selected.center_x - candidate.center_x) ** 2 + (
                selected.center_y - candidate.center_y
            ) ** 2
            if distance_squared <= radius_squared:
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
    return kept


def merge_detections(
    detections: list[Detection],
    global_nms_iou: float = 0.1,
    center_duplicate_radius_px: float = 8.0,
    enable_center_suppression: bool = False,
    max_detections_full_frame: int = 5000,
) -> tuple[list[Detection], MergeDiagnostics]:
    after_nms = class_aware_nms(detections, global_nms_iou)
    after_center = (
        center_distance_suppression(after_nms, center_duplicate_radius_px)
        if enable_center_suppression
        else after_nms
    )
    final = sorted(after_center, key=lambda item: item.confidence, reverse=True)[
        :max_detections_full_frame
    ]
    diagnostics = MergeDiagnostics(
        raw_tile_predictions=len(detections),
        after_global_nms=len(after_nms),
        after_center_suppression=len(final),
        duplicates_removed=len(detections) - len(final),
    )
    return final, diagnostics
