"""Reproducible AI-assisted surveyed analysis-area polygon generation."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from .common import list_to_matrix, point_in_polygon, quantiles, read_json, transform_points, write_json
from .plots import validate_polygon


LOGGER = logging.getLogger(__name__)


def _signed_area(vertices: np.ndarray) -> float:
    return float(0.5 * np.sum(vertices[:, 0] * np.roll(vertices[:, 1], -1) - np.roll(vertices[:, 0], -1) * vertices[:, 1]))


def _ensure_positive_orientation(vertices: np.ndarray) -> np.ndarray:
    return vertices if _signed_area(vertices) > 0 else vertices[::-1].copy()


def build_component_labels(
    image_shape: tuple[int, int],
    confirmed_preview_points: np.ndarray,
    median_spacing_preview_px: float,
    minimum_component_plants: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[dict[str, Any]], dict[str, Any]]:
    """Connect row-pattern support without bridging visible road-width separators."""
    height, width = image_shape
    raster = np.zeros((height, width), dtype=np.uint8)
    points = np.rint(confirmed_preview_points).astype(np.int32)
    valid = (
        (points[:, 0] >= 0)
        & (points[:, 0] < width)
        & (points[:, 1] >= 0)
        & (points[:, 1] < height)
    )
    points = points[valid]
    raster[points[:, 1], points[:, 0]] = 255
    dilation_radius = max(2, int(round(1.15 * median_spacing_preview_px)))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (2 * dilation_radius + 1, 2 * dilation_radius + 1)
    )
    support = cv2.dilate(raster, kernel)
    count, raw_labels, stats, centroids = cv2.connectedComponentsWithStats(support)
    raw_point_labels = raw_labels[points[:, 1], points[:, 0]]
    components = []
    for label in range(1, count):
        plant_count = int(np.sum(raw_point_labels == label))
        area = int(stats[label, cv2.CC_STAT_AREA])
        if plant_count < minimum_component_plants:
            continue
        x, y, component_width, component_height = map(int, stats[label, :4])
        components.append(
            {
                "raw_label": label,
                "plant_count": plant_count,
                "support_area_preview_px2": area,
                "bbox_preview": [x, y, component_width, component_height],
                "centroid_preview": [float(centroids[label, 0]), float(centroids[label, 1])],
            }
        )
    components.sort(key=lambda item: (item["centroid_preview"][1], item["centroid_preview"][0]))
    selected_labels = np.zeros_like(raw_labels, dtype=np.int32)
    for selected_index, component in enumerate(components, start=1):
        selected_labels[raw_labels == component["raw_label"]] = selected_index
        component["component_index"] = selected_index
    diagnostics = {
        "median_spacing_preview_px": median_spacing_preview_px,
        "dilation_radius_preview_px": dilation_radius,
        "dilation_radius_spacing_fraction": dilation_radius / median_spacing_preview_px,
        "minimum_component_plants": minimum_component_plants,
        "raw_connected_component_count": count - 1,
        "selected_component_count": len(components),
    }
    return raster, support, selected_labels, components, diagnostics


def _component_polygon(
    selected_labels: np.ndarray,
    component_index: int,
    median_spacing_preview_px: float,
    valid_coverage_mask: np.ndarray,
) -> np.ndarray:
    component = np.uint8(selected_labels == component_index) * 255
    # Coverage intersection protects the mosaic boundary; it never cuts internal plant-density holes.
    component[~valid_coverage_mask] = 0
    contours, _hierarchy = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        raise RuntimeError(f"Component {component_index} has no external contour")
    contour = max(contours, key=cv2.contourArea)
    epsilon = max(1.0, 1.35 * median_spacing_preview_px)
    approximated = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2).astype(np.float64)
    if len(approximated) < 3:
        raise RuntimeError(f"Component {component_index} simplified below three vertices")
    return _ensure_positive_orientation(approximated)


def _classify_component(component: Mapping[str, Any], mosaic_width: int) -> tuple[str, str]:
    _x, _y, width, _height = component["bbox_preview"]
    center_x = float(component["centroid_preview"][0])
    if width >= 0.38 * mosaic_width:
        return (
            "main_planted_block",
            "Separated from adjacent main blocks by a visually confirmed diagonal cross-road/headland and its junction area.",
        )
    if center_x < 0.50 * mosaic_width:
        return (
            "left_side_planted_strip",
            "Separated from the main planted block by the visually continuous light dirt road/headland corridor.",
        )
    return (
        "right_side_planted_strip",
        "Separated from the main planted block by the dark canal/right-side road corridor; cross-road splits are retained only where the plant support is disconnected.",
    )


def generate_ai_assisted_polygons(postprocess_run: Path) -> dict[str, Any]:
    postprocess_run = postprocess_run.resolve()
    mosaic = cv2.imread(str(postprocess_run / "survey_mosaic_preview.png"))
    if mosaic is None:
        raise FileNotFoundError("survey_mosaic_preview.png is missing or unreadable")
    height, width = mosaic.shape[:2]
    unique = pd.read_csv(postprocess_run / "unique_plants.csv")
    mosaic_transform = read_json(postprocess_run / "mosaic_transform.json")
    fusion_report = read_json(postprocess_run / "fusion_report.json")
    local_to_preview = list_to_matrix(mosaic_transform["local_to_preview"])
    preview_to_local = list_to_matrix(mosaic_transform["preview_to_local"])
    coverage = np.load(postprocess_run / "coverage" / "coverage_counts.npz")["coverage"]
    if coverage.shape != (height, width):
        raise ValueError("Coverage raster and mosaic dimensions differ")

    confirmed = unique[unique["support_status"] == "confirmed"].copy()
    low_support = unique[unique["support_status"] == "low_support"].copy()
    suspicious = unique[unique["support_status"] == "suspicious_singleton"].copy()
    confirmed_preview = transform_points(
        confirmed[["local_x", "local_y"]].to_numpy(dtype=np.float64), local_to_preview
    )
    low_preview = transform_points(
        low_support[["local_x", "local_y"]].to_numpy(dtype=np.float64), local_to_preview
    )
    suspicious_preview = transform_points(
        suspicious[["local_x", "local_y"]].to_numpy(dtype=np.float64), local_to_preview
    )
    round_trip = transform_points(confirmed_preview[:: max(1, len(confirmed_preview) // 1000)], preview_to_local)
    round_trip_source = confirmed[["local_x", "local_y"]].to_numpy(dtype=np.float64)[
        :: max(1, len(confirmed_preview) // 1000)
    ]
    round_trip_error = np.linalg.norm(round_trip - round_trip_source, axis=1)
    if float(round_trip_error.max()) > 1e-6:
        raise RuntimeError("Coordinate round-trip QA failed; refusing to create polygons")

    scale = float(local_to_preview[0, 0])
    median_spacing_local = float(fusion_report["association_radius_basis"]["median_plant_spacing_local_px"])
    median_spacing_preview = median_spacing_local * scale
    minimum_component_plants = max(250, int(round(0.0075 * len(confirmed))))
    raster, support, selected_labels, components, segmentation = build_component_labels(
        (height, width), confirmed_preview, median_spacing_preview, minimum_component_plants
    )
    # Gap analysis requires >=3 opportunities. Boundary construction uses the same evidence gate.
    valid_coverage = coverage >= 3
    local_confirmed_tree = cKDTree(
        confirmed[["local_x", "local_y"]].to_numpy(dtype=np.float64)
    )
    features = []
    qa_polygons = []
    polygon_masks: list[np.ndarray] = []
    for feature_index, component in enumerate(components, start=1):
        preview_vertices = _component_polygon(
            selected_labels, feature_index, median_spacing_preview, valid_coverage
        )
        local_vertices = _ensure_positive_orientation(
            transform_points(preview_vertices, preview_to_local)
        )
        geometry_valid, geometry_reason = validate_polygon(local_vertices.tolist())
        if not geometry_valid:
            raise RuntimeError(
                f"AI polygon {feature_index} failed geometry validation: {geometry_reason}"
            )
        polygon_mask = np.zeros((height, width), dtype=np.uint8)
        cv2.fillPoly(polygon_mask, [np.rint(preview_vertices).astype(np.int32)], 1)
        polygon_masks.append(polygon_mask)
        inside_confirmed = np.asarray(
            [point_in_polygon(point, local_vertices) for point in confirmed[["local_x", "local_y"]].to_numpy()]
        )
        inside_low = np.asarray(
            [point_in_polygon(point, local_vertices) for point in low_support[["local_x", "local_y"]].to_numpy()]
        )
        inside_suspicious = np.asarray(
            [point_in_polygon(point, local_vertices) for point in suspicious[["local_x", "local_y"]].to_numpy()]
        )
        inside_pixels = polygon_mask.astype(bool)
        valid_pixels = int(np.sum(inside_pixels & valid_coverage))
        total_pixels = int(np.sum(inside_pixels))
        noncoverage_pixels = int(np.sum(inside_pixels & (coverage == 0)))
        boundary_local = transform_points(preview_vertices, preview_to_local)
        boundary_distances, _ = local_confirmed_tree.query(boundary_local)
        area_local = abs(_signed_area(local_vertices))
        category, separation_reason = _classify_component(component, width)
        coverage_fraction = valid_pixels / max(1, total_pixels)
        confidence = "high" if coverage_fraction >= 0.995 and int(inside_confirmed.sum()) >= 1000 else "medium"
        plot_id = f"AI_AREA_{feature_index:03d}"
        ring = local_vertices.tolist() + [local_vertices[0].tolist()]
        feature = {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "plot_id": plot_id,
                "analysis_area_id": plot_id,
                "feature_type": "surveyed_analysis_area",
                "source": "ai_assisted_visual_mosaic_unique_plants_coverage",
                "coordinate_space": "local_map",
                "source_postprocess_run": "postprocess_run_20260721_111300",
                "version": "v1",
                "is_official_plot_boundary": False,
                "is_cadastral": False,
                "confidence": confidence,
                "boundary_status": "visible_covered_planted_support_boundary",
                "notes": f"{category}. {separation_reason} Internal low-density regions are deliberately retained as possible gaps.",
                "separation_reason": separation_reason,
            },
        }
        features.append(feature)
        qa_polygons.append(
            {
                "plot_id": plot_id,
                "category": category,
                "geometry_valid": geometry_valid,
                "geometry_reason": geometry_reason,
                "vertex_count": len(local_vertices),
                "area_local_px2": area_local,
                "confirmed_unique_plants_inside": int(inside_confirmed.sum()),
                "low_support_candidates_inside": int(inside_low.sum()),
                "suspicious_singletons_inside": int(inside_suspicious.sum()),
                "valid_coverage_fraction": coverage_fraction,
                "noncoverage_preview_pixels_inside": noncoverage_pixels,
                "boundary_to_nearest_confirmed_plant_local_px": quantiles(boundary_distances),
                "connected_component_count": 1,
                "bbox_preview": component["bbox_preview"],
                "centroid_preview": component["centroid_preview"],
                "separation_reason": separation_reason,
            }
        )

    overlap_pixels = 0
    for first in range(len(polygon_masks)):
        for second in range(first + 1, len(polygon_masks)):
            overlap_pixels += int(np.sum((polygon_masks[first] > 0) & (polygon_masks[second] > 0)))
    if overlap_pixels:
        raise RuntimeError(f"AI-assisted polygons overlap by {overlap_pixels} preview pixels")

    geojson = {
        "type": "FeatureCollection",
        "coordinate_space": "local_map",
        "crs": None,
        "manual_status": "ai_assisted_polygons_saved",
        "boundary_semantics": "AI-assisted surveyed analysis area based on video mosaic, coverage, and unique-plant distribution; not an official or cadastral plot boundary.",
        "source_postprocess_run": "postprocess_run_20260721_111300",
        "version": "v1",
        "features": features,
    }
    output_path = postprocess_run / "plots_local_ai_assisted_v1.geojson"
    write_json(output_path, geojson)
    debug_dir = postprocess_run / "plots_ai_assisted_debug"
    debug_dir.mkdir(exist_ok=True)
    _write_debug_masks(debug_dir, mosaic, raster, support, selected_labels, valid_coverage, polygon_masks)
    overlay = _write_overlay(
        postprocess_run,
        mosaic,
        features,
        local_to_preview,
        confirmed_preview,
        low_preview,
        suspicious_preview,
        coverage,
    )
    _write_critical_crops(debug_dir, overlay)
    qa = {
        "status": "passed_geometry_and_coordinate_QA_visual_review_required",
        "source": str(postprocess_run),
        "coordinate_space": "local_map",
        "mosaic_size": [width, height],
        "coordinate_round_trip_error_local_px": quantiles(round_trip_error),
        "median_plant_spacing_local_px": median_spacing_local,
        "median_plant_spacing_preview_px": median_spacing_preview,
        "coverage_gate_processed_frames": 3,
        "segmentation": segmentation,
        "polygon_count": len(features),
        "polygon_overlap_preview_pixels": overlap_pixels,
        "polygons": qa_polygons,
        "method": [
            "confirmed unique plants transformed into preview coordinates",
            "plant support connected with dilation radius derived from median plant spacing",
            "components below data-derived plant-support count removed",
            "components intersected with valid coverage without removing internal low-density holes",
            "external component boundary simplified at an epsilon derived from plant spacing",
            "preview vertices transformed to local-map coordinates and round-tripped for QA",
        ],
        "limitations": [
            "boundaries are surveyed analysis areas, not official/cadastral plots",
            "RGB texture is used for visual QA; confirmed plant support and coverage drive reproducible boundaries",
            "WGS84 georeferencing failed QA and is not used",
        ],
    }
    write_json(postprocess_run / "plots_local_ai_assisted_v1_qa.json", qa)
    (postprocess_run / "plots_local_ai_assisted_v1_qa.md").write_text(
        _qa_markdown(qa), encoding="utf-8"
    )
    LOGGER.info("Wrote %d AI-assisted surveyed analysis areas to %s", len(features), output_path)
    return qa


def _write_debug_masks(
    debug_dir: Path,
    mosaic: np.ndarray,
    raster: np.ndarray,
    support: np.ndarray,
    labels: np.ndarray,
    valid_coverage: np.ndarray,
    polygon_masks: Sequence[np.ndarray],
) -> None:
    cv2.imwrite(str(debug_dir / "confirmed_plant_raster.png"), raster)
    cv2.imwrite(str(debug_dir / "plant_support_mask.png"), support)
    cv2.imwrite(str(debug_dir / "valid_coverage_mask.png"), np.uint8(valid_coverage) * 255)
    union = np.zeros_like(raster)
    for mask in polygon_masks:
        union[mask > 0] = 255
    cv2.imwrite(str(debug_dir / "final_polygon_union_mask.png"), union)
    excluded = mosaic.copy()
    excluded_area = valid_coverage & (union == 0)
    excluded[excluded_area] = (0.45 * excluded[excluded_area] + 0.55 * np.asarray([0, 0, 255])).astype(np.uint8)
    cv2.imwrite(str(debug_dir / "excluded_valid_coverage_visual.png"), excluded)
    color_labels = np.zeros_like(mosaic)
    for label in range(1, int(labels.max()) + 1):
        color = np.random.default_rng(label).integers(70, 245, 3, dtype=np.uint8)
        color_labels[labels == label] = color
    cv2.imwrite(str(debug_dir / "selected_component_labels.png"), color_labels)


def _write_overlay(
    postprocess_run: Path,
    mosaic: np.ndarray,
    features: Sequence[Mapping[str, Any]],
    local_to_preview: np.ndarray,
    confirmed: np.ndarray,
    low: np.ndarray,
    suspicious: np.ndarray,
    coverage: np.ndarray,
) -> np.ndarray:
    overlay = mosaic.copy()
    low_coverage = (coverage > 0) & (coverage < 3)
    overlay[low_coverage] = (
        0.45 * overlay[low_coverage] + 0.55 * np.asarray([255, 255, 0])
    ).astype(np.uint8)
    fill = overlay.copy()
    palette = [
        (255, 90, 40),
        (60, 180, 255),
        (180, 80, 255),
        (80, 220, 160),
        (255, 180, 60),
        (150, 255, 80),
    ]
    for index, feature in enumerate(features):
        local = np.asarray(feature["geometry"]["coordinates"][0][:-1], dtype=np.float64)
        preview = np.rint(transform_points(local, local_to_preview)).astype(np.int32)
        color = palette[index % len(palette)]
        cv2.fillPoly(fill, [preview], color)
    overlay = cv2.addWeighted(overlay, 0.70, fill, 0.30, 0)
    for points, color, radius in [
        (confirmed, (30, 230, 30), 1),
        (low, (0, 180, 255), 2),
        (suspicious, (0, 0, 255), 2),
    ]:
        for x, y in np.rint(points).astype(int):
            if 0 <= x < overlay.shape[1] and 0 <= y < overlay.shape[0]:
                cv2.circle(overlay, (x, y), radius, color, -1)
    for feature in features:
        local = np.asarray(feature["geometry"]["coordinates"][0][:-1], dtype=np.float64)
        preview = np.rint(transform_points(local, local_to_preview)).astype(np.int32)
        cv2.polylines(overlay, [preview], True, (255, 255, 255), 2)
        centroid = np.rint(preview.mean(axis=0)).astype(int)
        cv2.putText(
            overlay,
            str(feature["properties"]["plot_id"]),
            (int(centroid[0]) - 30, int(centroid[1])),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    path = postprocess_run / "plots_local_ai_assisted_v1_overlay.png"
    cv2.imwrite(str(path), overlay)
    return overlay


def _write_critical_crops(debug_dir: Path, overlay: np.ndarray) -> None:
    windows = [
        (0, 520, "top_boundary_and_first_diagonal_road"),
        (360, 960, "first_junction_and_second_main_block"),
        (820, 1360, "second_diagonal_road_and_right_corridor"),
        (1260, 1760, "third_diagonal_road_and_left_headland"),
        (1680, 2160, "large_internal_bare_area_and_fourth_road"),
        (1980, overlay.shape[0], "bottom_boundary_and_coverage_edge"),
    ]
    for index, (start, end, label) in enumerate(windows, start=1):
        crop = overlay[max(0, start) : min(overlay.shape[0], end)]
        crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
        cv2.imwrite(str(debug_dir / f"qa_crop_{index:02d}_{label}.png"), crop)


def _qa_markdown(qa: Mapping[str, Any]) -> str:
    lines = [
        "# AI-assisted plot polygon QA",
        "",
        "**Boundary meaning:** AI-assisted surveyed analysis area based on video mosaic, coverage, and unique-plant distribution; not an official or cadastral plot boundary.",
        "",
        f"- Polygon count: {qa['polygon_count']}",
        f"- Mosaic size: {qa['mosaic_size'][0]} × {qa['mosaic_size'][1]}",
        f"- Local/preview/local round-trip maximum error: {qa['coordinate_round_trip_error_local_px']['max']:.3e} local px",
        f"- Median plant spacing: {qa['median_plant_spacing_local_px']:.3f} local px / {qa['median_plant_spacing_preview_px']:.3f} preview px",
        f"- Polygon overlap: {qa['polygon_overlap_preview_pixels']} preview pixels",
        "",
        "## Per polygon",
        "",
        "| plot_id | category | vertices | area local px² | confirmed | low-support | suspicious | valid coverage | noncoverage px | geometry |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in qa["polygons"]:
        lines.append(
            f"| {row['plot_id']} | {row['category']} | {row['vertex_count']} | {row['area_local_px2']:.1f} | {row['confirmed_unique_plants_inside']} | {row['low_support_candidates_inside']} | {row['suspicious_singletons_inside']} | {row['valid_coverage_fraction']:.4f} | {row['noncoverage_preview_pixels_inside']} | {row['geometry_valid']} |"
        )
    lines.extend(
        [
            "",
            "## Visual review checklist",
            "",
            "- White boundaries must remain inside planted support and must not bridge diagonal roads.",
            "- The dark right-side canal/road corridor must remain outside all main-block polygons.",
            "- Junction soil and light dirt-road corridors must remain outside the analysis union.",
            "- Internal bare areas surrounded by rows are intentionally retained for gap analysis.",
            "- Low-coverage cyan pixels and black nodata are excluded.",
            "- Review `plots_ai_assisted_debug/qa_crop_*.png` before running gap analysis.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate AI-assisted local surveyed analysis polygons")
    parser.add_argument("--postprocess-run", required=True, type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    qa = generate_ai_assisted_polygons(args.postprocess_run)
    print(f"generated {qa['polygon_count']} polygons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
