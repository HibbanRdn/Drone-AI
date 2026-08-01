"""Memory-safe segmentation union, morphology, gating, and local-area helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .common import matrix_to_list, transform_points, write_json


AREA_DESCRIPTION = (
    "Segmentation-derived local analysis area; not an official or cadastral plot boundary."
)


def predicted_union_binary(
    instance_masks: Any,
    class_ids: Sequence[int] | np.ndarray | None = None,
    class_id: int = 0,
    target_shape: tuple[int, int] | None = None,
) -> np.ndarray:
    """Return one uint8 semantic union from diagnostic instance masks."""
    if instance_masks is None:
        if target_shape is None:
            raise ValueError("target_shape is required when instance_masks is None")
        return np.zeros(target_shape, dtype=np.uint8)
    if hasattr(instance_masks, "detach"):
        instance_masks = instance_masks.detach().cpu().numpy()
    masks = np.asarray(instance_masks)
    if masks.ndim == 2:
        masks = masks[None, ...]
    if masks.ndim != 3:
        raise ValueError(f"Expected masks shaped (N,H,W), got {masks.shape}")
    if class_ids is not None:
        classes = np.asarray(class_ids, dtype=np.int64)
        if len(classes) != len(masks):
            raise ValueError("class_ids and instance_masks length mismatch")
        masks = masks[classes == int(class_id)]
    if len(masks):
        union = np.any(masks > 0.5, axis=0).astype(np.uint8) * 255
    else:
        union = np.zeros(masks.shape[1:], dtype=np.uint8)
    if target_shape is not None and union.shape != target_shape:
        union = cv2.resize(
            union,
            (int(target_shape[1]), int(target_shape[0])),
            interpolation=cv2.INTER_NEAREST,
        )
    return np.where(union > 0, 255, 0).astype(np.uint8)


def apply_segmentation_morphology(mask: np.ndarray, variant: str) -> np.ndarray:
    """Apply the locked light morphology variant to a semantic union mask."""
    binary = np.where(np.asarray(mask) > 0, 255, 0).astype(np.uint8)
    if variant == "none":
        return binary.copy()
    if variant != "closing_3":
        raise ValueError(f"Unsupported segmentation morphology: {variant}")
    kernel = np.ones((3, 3), dtype=np.uint8)
    return cv2.morphologyEx(
        binary,
        cv2.MORPH_CLOSE,
        kernel,
        iterations=1,
    )


def classify_points_by_mask(
    processed_mask: np.ndarray,
    points_xy: np.ndarray,
    boundary_buffer_px: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Classify points as safe interior, boundary review, or outside."""
    mask = (np.asarray(processed_mask) > 0).astype(np.uint8)
    points = np.asarray(points_xy, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 2:
        raise ValueError("points_xy must have shape (N,2)")
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    distance = cv2.distanceTransform(padded, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)[1:-1, 1:-1]
    height, width = mask.shape
    xi = np.rint(points[:, 0]).astype(np.int64)
    yi = np.rint(points[:, 1]).astype(np.int64)
    in_bounds = (xi >= 0) & (xi < width) & (yi >= 0) & (yi < height)
    inside = np.zeros(len(points), dtype=bool)
    boundary_distance = np.zeros(len(points), dtype=np.float32)
    valid_indices = np.flatnonzero(in_bounds)
    inside[valid_indices] = mask[yi[valid_indices], xi[valid_indices]] > 0
    boundary_distance[valid_indices] = distance[yi[valid_indices], xi[valid_indices]]
    status = np.full(len(points), "outside_rejected", dtype=object)
    status[inside] = "boundary_review"
    status[inside & (boundary_distance >= float(boundary_buffer_px))] = "accepted"
    return status, boundary_distance


def _closed_ring(points: np.ndarray) -> list[list[float]]:
    ring = [[float(x), float(y)] for x, y in points]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def extract_local_area_features(
    binary_preview_mask: np.ndarray,
    preview_to_local: np.ndarray,
    minimum_component_area_preview_px2: float = 100.0,
    simplify_preview_px: float = 1.0,
) -> list[dict[str, Any]]:
    """Extract Polygon features, including holes, from a preview-space union."""
    mask = np.where(np.asarray(binary_preview_mask) > 0, 255, 0).astype(np.uint8)
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return []
    hierarchy = hierarchy[0]
    candidates: list[tuple[float, int]] = []
    for index, contour in enumerate(contours):
        if hierarchy[index][3] == -1:
            area = float(abs(cv2.contourArea(contour)))
            if area >= float(minimum_component_area_preview_px2):
                candidates.append((area, index))
    candidates.sort(reverse=True)
    features: list[dict[str, Any]] = []
    local_area_scale = abs(float(np.linalg.det(np.asarray(preview_to_local)[:2, :2])))
    for area_index, (preview_area, contour_index) in enumerate(candidates, start=1):
        rings: list[list[list[float]]] = []
        outer = contours[contour_index].reshape(-1, 2)
        if len(outer) < 3:
            continue
        rings.append(_closed_ring(transform_points(outer, preview_to_local)))
        hole_count = 0
        child = int(hierarchy[contour_index][2])
        while child >= 0:
            hole = contours[child].reshape(-1, 2)
            if len(hole) >= 3:
                rings.append(_closed_ring(transform_points(hole, preview_to_local)))
                hole_count += 1
            child = int(hierarchy[child][0])
        area_id = f"SEG_AREA_{area_index:03d}"
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": rings},
                "properties": {
                    "plot_id": area_id,
                    "area_id": area_id,
                    "source": "segmentation_plantable_area_union_closing_3_temporal_majority",
                    "class_name": "plantable_area",
                    "description": AREA_DESCRIPTION,
                    "is_official_plot_boundary": False,
                    "coordinate_space": "registered_local_pixels",
                    "preview_area_px2": preview_area,
                    "estimated_local_area_px2": preview_area * local_area_scale,
                    "hole_count": hole_count,
                },
            }
        )
    return features


def write_local_areas_geojson(
    path: Path,
    features: list[dict[str, Any]],
    *,
    aggregation: Mapping[str, Any],
    preview_to_local: np.ndarray,
) -> Path:
    payload = {
        "type": "FeatureCollection",
        "coordinate_space": "registered_local_pixels",
        "coordinate_reference_status": "local_only_not_wgs84",
        "description": AREA_DESCRIPTION,
        "canonical_segmentation_outputs": [
            "predicted_union_binary",
            "union_polygon",
        ],
        "aggregation": dict(aggregation),
        "preview_to_local": matrix_to_list(preview_to_local),
        "features": features,
    }
    return write_json(path, payload)


def rasterize_local_geojson(
    payload: Mapping[str, Any],
    local_to_preview: np.ndarray,
    preview_shape: tuple[int, int],
) -> np.ndarray:
    """Rasterize Polygon/MultiPolygon local geometry, respecting holes."""
    raster = np.zeros(preview_shape, dtype=np.uint8)
    for feature in payload.get("features", []):
        geometry = feature.get("geometry", {})
        geometry_type = geometry.get("type")
        coordinates = geometry.get("coordinates", [])
        polygons = coordinates if geometry_type == "MultiPolygon" else [coordinates]
        if geometry_type not in {"Polygon", "MultiPolygon"}:
            continue
        for polygon in polygons:
            if not polygon:
                continue
            outer = np.rint(
                transform_points(np.asarray(polygon[0], dtype=np.float64), local_to_preview)
            ).astype(np.int32)
            if len(outer) >= 3:
                cv2.fillPoly(raster, [outer], 1)
            for hole in polygon[1:]:
                inner = np.rint(
                    transform_points(np.asarray(hole, dtype=np.float64), local_to_preview)
                ).astype(np.int32)
                if len(inner) >= 3:
                    cv2.fillPoly(raster, [inner], 0)
    return raster
