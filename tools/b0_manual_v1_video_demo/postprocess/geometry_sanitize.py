"""Shared polygon sanitation and QA for segmentation-derived analysis areas."""

from __future__ import annotations

import copy
import logging
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.validation import explain_validity

from .common import read_json, utc_now_iso, write_json


LOGGER = logging.getLogger(__name__)

try:
    from shapely import make_valid as _shapely_make_valid
except ImportError:  # Shapely 1.8 compatibility
    from shapely.validation import make_valid as _shapely_make_valid

try:
    from shapely import orient_polygons as _orient_polygons
except ImportError:  # Shapely <2.1 compatibility
    _orient_polygons = None


class GeometrySanitizationError(ValueError):
    """Raised when a feature collection has no usable polygonal geometry."""


def iter_polygon_rings(
    geometry: Mapping[str, Any],
) -> list[tuple[Sequence[Sequence[float]], bool]]:
    """Return every Polygon/MultiPolygon ring with an interior-ring flag."""
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    polygons = coordinates if geometry_type == "MultiPolygon" else [coordinates]
    if geometry_type not in {"Polygon", "MultiPolygon"}:
        return []
    return [
        (ring, ring_index > 0)
        for polygon in polygons
        for ring_index, ring in enumerate(polygon)
    ]


def _same_point(first: Sequence[float], second: Sequence[float]) -> bool:
    return float(first[0]) == float(second[0]) and float(first[1]) == float(second[1])


def _clean_ring(
    coordinates: Sequence[Sequence[float]],
) -> tuple[list[list[float]] | None, dict[str, Any]]:
    cleaned: list[list[float]] = []
    invalid_coordinate_count = 0
    consecutive_duplicates = 0
    for coordinate in coordinates:
        if len(coordinate) < 2:
            invalid_coordinate_count += 1
            continue
        point = [float(coordinate[0]), float(coordinate[1])]
        if not all(math.isfinite(value) for value in point):
            invalid_coordinate_count += 1
            continue
        if cleaned and _same_point(cleaned[-1], point):
            consecutive_duplicates += 1
            continue
        cleaned.append(point)
    while len(cleaned) > 1 and _same_point(cleaned[-1], cleaned[0]):
        cleaned.pop()
    unique_points = len({(point[0], point[1]) for point in cleaned})
    if unique_points < 3:
        return None, {
            "consecutive_duplicate_vertices_removed": consecutive_duplicates,
            "invalid_coordinates_removed": invalid_coordinate_count,
            "unique_vertices": unique_points,
            "reason": "fewer_than_three_unique_vertices",
        }
    cleaned.append(cleaned[0].copy())
    return cleaned, {
        "consecutive_duplicate_vertices_removed": consecutive_duplicates,
        "invalid_coordinates_removed": invalid_coordinate_count,
        "unique_vertices": unique_points,
        "reason": "usable",
    }


def _clean_polygon_coordinates(
    coordinates: Sequence[Sequence[Sequence[float]]],
) -> tuple[list[list[list[float]]] | None, dict[str, Any]]:
    if not coordinates:
        return None, {
            "consecutive_duplicate_vertices_removed": 0,
            "invalid_coordinates_removed": 0,
            "degenerate_holes_dropped": 0,
            "reason": "missing_exterior_ring",
        }
    exterior, exterior_qa = _clean_ring(coordinates[0])
    totals = {
        "consecutive_duplicate_vertices_removed": int(
            exterior_qa["consecutive_duplicate_vertices_removed"]
        ),
        "invalid_coordinates_removed": int(exterior_qa["invalid_coordinates_removed"]),
        "degenerate_holes_dropped": 0,
        "reason": exterior_qa["reason"],
    }
    if exterior is None:
        return None, totals
    rings = [exterior]
    for hole in coordinates[1:]:
        cleaned_hole, hole_qa = _clean_ring(hole)
        totals["consecutive_duplicate_vertices_removed"] += int(
            hole_qa["consecutive_duplicate_vertices_removed"]
        )
        totals["invalid_coordinates_removed"] += int(hole_qa["invalid_coordinates_removed"])
        if cleaned_hole is None:
            totals["degenerate_holes_dropped"] += 1
            continue
        rings.append(cleaned_hole)
    totals["reason"] = "usable"
    return rings, totals


def _clean_geometry_mapping(
    geometry: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    geometry_type = str(geometry.get("type", ""))
    coordinate_qa = {
        "consecutive_duplicate_vertices_removed": 0,
        "invalid_coordinates_removed": 0,
        "degenerate_holes_dropped": 0,
        "degenerate_polygons_dropped": 0,
    }
    if geometry_type == "Polygon":
        polygon, qa = _clean_polygon_coordinates(geometry.get("coordinates", []))
        for key in coordinate_qa:
            coordinate_qa[key] += int(qa.get(key, 0))
        if polygon is None:
            coordinate_qa["degenerate_polygons_dropped"] += 1
            return None, coordinate_qa
        return {"type": "Polygon", "coordinates": polygon}, coordinate_qa
    if geometry_type == "MultiPolygon":
        polygons = []
        for raw_polygon in geometry.get("coordinates", []):
            polygon, qa = _clean_polygon_coordinates(raw_polygon)
            for key in coordinate_qa:
                coordinate_qa[key] += int(qa.get(key, 0))
            if polygon is None:
                coordinate_qa["degenerate_polygons_dropped"] += 1
                continue
            polygons.append(polygon)
        if not polygons:
            return None, coordinate_qa
        return {"type": "MultiPolygon", "coordinates": polygons}, coordinate_qa
    if geometry_type == "GeometryCollection":
        geometries = []
        for child in geometry.get("geometries", []):
            cleaned_child, child_qa = _clean_geometry_mapping(child)
            for key in coordinate_qa:
                coordinate_qa[key] += int(child_qa.get(key, 0))
            if cleaned_child is not None:
                geometries.append(cleaned_child)
        if not geometries:
            return None, coordinate_qa
        return {"type": "GeometryCollection", "geometries": geometries}, coordinate_qa
    return copy.deepcopy(dict(geometry)), coordinate_qa


def _polygonal_components(geometry: BaseGeometry) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, (MultiPolygon, GeometryCollection)):
        components: list[Polygon] = []
        for child in geometry.geoms:
            components.extend(_polygonal_components(child))
        return components
    return []


def _make_valid_compatible(geometry: BaseGeometry) -> tuple[BaseGeometry, str]:
    try:
        return (
            _shapely_make_valid(
                geometry,
                method="structure",
                keep_collapsed=False,
            ),
            "make_valid_structure",
        )
    except TypeError:
        return _shapely_make_valid(geometry), "make_valid_default"


def _orient_polygonal(geometry: BaseGeometry) -> BaseGeometry:
    if _orient_polygons is not None:
        return _orient_polygons(geometry, exterior_cw=False)
    if isinstance(geometry, Polygon):
        return orient(geometry, sign=1.0)
    if isinstance(geometry, MultiPolygon):
        return MultiPolygon([orient(part, sign=1.0) for part in geometry.geoms])
    return geometry


def _drop_small_holes(
    polygon: Polygon,
    minimum_hole_area: float,
) -> tuple[Polygon, int, float]:
    retained_holes = []
    dropped_count = 0
    dropped_area = 0.0
    for ring in polygon.interiors:
        hole_polygon = Polygon(ring)
        area = float(abs(hole_polygon.area))
        if hole_polygon.is_empty or area < minimum_hole_area:
            dropped_count += 1
            dropped_area += area
            continue
        retained_holes.append(list(ring.coords))
    return Polygon(polygon.exterior.coords, retained_holes), dropped_count, dropped_area


def _assemble_polygonal_geometry(
    geometry: BaseGeometry,
    *,
    minimum_component_area: float,
    minimum_hole_area: float,
) -> tuple[BaseGeometry | None, dict[str, Any]]:
    retained: list[Polygon] = []
    dropped_sliver_count = 0
    dropped_sliver_area = 0.0
    dropped_hole_count = 0
    dropped_hole_area = 0.0
    for component in _polygonal_components(geometry):
        if component.is_empty or float(component.area) < minimum_component_area:
            dropped_sliver_count += 1
            dropped_sliver_area += float(component.area)
            continue
        component, hole_count, hole_area = _drop_small_holes(
            component,
            minimum_hole_area,
        )
        dropped_hole_count += hole_count
        dropped_hole_area += hole_area
        if component.is_empty or float(component.area) < minimum_component_area:
            dropped_sliver_count += 1
            dropped_sliver_area += float(component.area)
            continue
        retained.append(component)
    diagnostics = {
        "sliver_components_dropped": dropped_sliver_count,
        "sliver_area_dropped": dropped_sliver_area,
        "holes_dropped": dropped_hole_count,
        "hole_area_dropped": dropped_hole_area,
    }
    if not retained:
        return None, diagnostics
    merged = unary_union(retained)
    components = _polygonal_components(merged)
    if not components:
        return None, diagnostics
    ordered = sorted(
        components,
        key=lambda item: (
            -float(item.area),
            tuple(float(value) for value in item.bounds),
            item.wkb_hex,
        ),
    )
    result: BaseGeometry = ordered[0] if len(ordered) == 1 else MultiPolygon(ordered)
    return _orient_polygonal(result), diagnostics


def validate_polygonal_geometry(geometry: Mapping[str, Any]) -> tuple[bool, str]:
    """Validate a GeoJSON Polygon/MultiPolygon as one complete geometry."""
    if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        return False, "geometry_type_is_not_polygonal"
    try:
        value = shape(geometry)
    except Exception as exc:
        return False, f"geometry_construction_failed: {exc}"
    if value.is_empty:
        return False, "geometry_is_empty"
    if not value.is_valid:
        return False, explain_validity(value)
    if not _polygonal_components(value):
        return False, "geometry_has_no_polygonal_component"
    return True, "Valid Geometry"


def sanitize_geometry(
    geometry: Mapping[str, Any],
    *,
    minimum_component_area: float = 1.0,
    minimum_hole_area: float = 1.0,
    simplify_tolerance: float = 0.0,
    material_area_change_fraction: float = 0.02,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Repair a complete GeoJSON geometry and return an auditable diagnostic."""
    geometry_before = copy.deepcopy(dict(geometry))
    type_before = str(geometry_before.get("type", "unknown"))
    try:
        original_shape = shape(geometry_before)
        valid_before = bool(original_shape.is_valid and not original_shape.is_empty)
        validity_reason_before = (
            "Valid Geometry" if valid_before else explain_validity(original_shape)
        )
        area_before = float(original_shape.area)
    except Exception as exc:
        original_shape = None
        valid_before = False
        validity_reason_before = f"geometry_construction_failed: {exc}"
        area_before = None

    cleaned_mapping, coordinate_qa = _clean_geometry_mapping(geometry_before)
    diagnostic: dict[str, Any] = {
        "valid_before": valid_before,
        "validity_reason_before": validity_reason_before,
        "geometry_type_before": type_before,
        "area_before": area_before,
        "valid_after": False,
        "validity_reason_after": "not_repaired",
        "geometry_type_after": None,
        "area_after": None,
        "area_change_fraction": None,
        "component_count": 0,
        "holes_after": 0,
        "make_valid_method": None,
        "buffer_zero_fallback_used": False,
        "simplification_tolerance": float(simplify_tolerance),
        "material_area_change": False,
        "status": "unrecoverable",
        **coordinate_qa,
        "sliver_components_dropped": 0,
        "sliver_area_dropped": 0.0,
        "holes_dropped": int(coordinate_qa["degenerate_holes_dropped"]),
        "hole_area_dropped": 0.0,
    }
    if cleaned_mapping is None:
        diagnostic["validity_reason_after"] = "coordinate_cleanup_removed_all_polygonal_rings"
        return None, diagnostic
    try:
        working = shape(cleaned_mapping)
    except Exception as exc:
        diagnostic["validity_reason_after"] = f"geometry_construction_failed_after_cleanup: {exc}"
        return None, diagnostic

    if not working.is_valid:
        working, method = _make_valid_compatible(working)
        diagnostic["make_valid_method"] = method
    polygonal, filtering = _assemble_polygonal_geometry(
        working,
        minimum_component_area=float(minimum_component_area),
        minimum_hole_area=float(minimum_hole_area),
    )
    for key, value in filtering.items():
        diagnostic[key] += value
    if polygonal is None or not polygonal.is_valid:
        buffered = working.buffer(0)
        diagnostic["buffer_zero_fallback_used"] = True
        polygonal, filtering = _assemble_polygonal_geometry(
            buffered,
            minimum_component_area=float(minimum_component_area),
            minimum_hole_area=float(minimum_hole_area),
        )
        for key, value in filtering.items():
            diagnostic[key] += value
    if polygonal is None:
        diagnostic["validity_reason_after"] = "repair_produced_no_significant_polygonal_component"
        return None, diagnostic

    if simplify_tolerance > 0:
        polygonal = polygonal.simplify(
            float(simplify_tolerance),
            preserve_topology=True,
        )
        if not polygonal.is_valid:
            polygonal, method = _make_valid_compatible(polygonal)
            diagnostic["make_valid_method"] = (
                f"{diagnostic['make_valid_method']}+{method}"
                if diagnostic["make_valid_method"]
                else method
            )
        polygonal, filtering = _assemble_polygonal_geometry(
            polygonal,
            minimum_component_area=float(minimum_component_area),
            minimum_hole_area=float(minimum_hole_area),
        )
        for key, value in filtering.items():
            diagnostic[key] += value
    if polygonal is None or polygonal.is_empty or not polygonal.is_valid:
        diagnostic["validity_reason_after"] = (
            "simplification_or_repair_did_not_produce_valid_polygonal_geometry"
        )
        return None, diagnostic

    polygonal = _orient_polygonal(polygonal)
    output = mapping(polygonal)
    area_after = float(polygonal.area)
    area_change_fraction = (
        None
        if area_before is None
        else abs(area_after - area_before)
        / max(abs(area_before), float(minimum_component_area))
    )
    material_change = bool(
        area_change_fraction is not None
        and area_change_fraction > float(material_area_change_fraction)
    )
    components = _polygonal_components(polygonal)
    holes_after = sum(len(component.interiors) for component in components)
    valid_after, validity_reason_after = validate_polygonal_geometry(output)
    geometrically_unchanged = bool(
        original_shape is not None
        and valid_before
        and original_shape.equals(polygonal)
        and diagnostic["consecutive_duplicate_vertices_removed"] == 0
        and diagnostic["invalid_coordinates_removed"] == 0
        and diagnostic["degenerate_holes_dropped"] == 0
        and diagnostic["sliver_components_dropped"] == 0
        and diagnostic["holes_dropped"] == 0
        and simplify_tolerance == 0
    )
    diagnostic.update(
        {
            "valid_after": valid_after,
            "validity_reason_after": validity_reason_after,
            "geometry_type_after": output["type"],
            "area_after": area_after,
            "area_change_fraction": area_change_fraction,
            "component_count": len(components),
            "holes_after": holes_after,
            "material_area_change": material_change,
            "status": "unchanged" if geometrically_unchanged else "repaired",
        }
    )
    return output, diagnostic


def sanitize_feature_collection(
    payload: Mapping[str, Any],
    *,
    minimum_component_area: float = 1.0,
    minimum_hole_area: float = 1.0,
    simplify_tolerance: float = 0.0,
    material_area_change_fraction: float = 0.02,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Sanitize every feature independently while preserving all safe areas."""
    sanitized = copy.deepcopy(dict(payload))
    sanitized_features = []
    feature_reports = []
    for feature_index, feature in enumerate(payload.get("features", []), start=1):
        properties = copy.deepcopy(dict(feature.get("properties", {})))
        feature_id = str(
            properties.get("plot_id")
            or properties.get("area_id")
            or feature.get("id")
            or f"feature_{feature_index:03d}"
        )
        try:
            geometry, diagnostic = sanitize_geometry(
                feature.get("geometry", {}),
                minimum_component_area=minimum_component_area,
                minimum_hole_area=minimum_hole_area,
                simplify_tolerance=simplify_tolerance,
                material_area_change_fraction=material_area_change_fraction,
            )
        except Exception as exc:
            geometry = None
            diagnostic = {
                "valid_before": False,
                "validity_reason_before": f"sanitizer_exception: {exc}",
                "geometry_type_before": str(
                    feature.get("geometry", {}).get("type", "unknown")
                ),
                "area_before": None,
                "valid_after": False,
                "validity_reason_after": f"sanitizer_exception: {exc}",
                "geometry_type_after": None,
                "area_after": None,
                "area_change_fraction": None,
                "component_count": 0,
                "holes_after": 0,
                "sliver_components_dropped": 0,
                "sliver_area_dropped": 0.0,
                "holes_dropped": 0,
                "hole_area_dropped": 0.0,
                "material_area_change": False,
                "status": "unrecoverable",
            }
        diagnostic["feature_id"] = feature_id
        feature_reports.append(diagnostic)
        if geometry is None:
            LOGGER.warning(
                "Polygon geometry %s is unrecoverable and will not be analyzed: %s",
                feature_id,
                diagnostic["validity_reason_after"],
            )
            continue
        properties["geometry_sanitization_status"] = diagnostic["status"]
        properties["geometry_component_count"] = diagnostic["component_count"]
        properties["geometry_component_ids"] = [
            f"{feature_id}__component_{index:03d}"
            for index in range(1, int(diagnostic["component_count"]) + 1)
        ]
        properties["estimated_local_area_px2"] = diagnostic["area_after"]
        properties["hole_count"] = diagnostic["holes_after"]
        sanitized_features.append(
            {
                **copy.deepcopy(dict(feature)),
                "geometry": geometry,
                "properties": properties,
            }
        )
    if not sanitized_features:
        raise GeometrySanitizationError(
            "No valid analysis polygon remains after geometry sanitation"
        )
    sanitized["features"] = sanitized_features
    counts = {
        status: sum(report["status"] == status for report in feature_reports)
        for status in ("unchanged", "repaired", "unrecoverable")
    }
    needs_review = bool(
        counts["unrecoverable"]
        or any(report.get("material_area_change") for report in feature_reports)
    )
    report = {
        "generated_at": utc_now_iso(),
        "status": "needs_review" if needs_review else "passed",
        "thresholds": {
            "minimum_component_area": float(minimum_component_area),
            "minimum_hole_area": float(minimum_hole_area),
            "simplify_tolerance": float(simplify_tolerance),
            "material_area_change_fraction": float(material_area_change_fraction),
        },
        "summary": {
            "input_feature_count": len(payload.get("features", [])),
            "output_feature_count": len(sanitized_features),
            "polygonal_component_count": sum(
                int(report["component_count"])
                for report in feature_reports
                if report["status"] != "unrecoverable"
            ),
            "unchanged": counts["unchanged"],
            "repaired": counts["repaired"],
            "unrecoverable": counts["unrecoverable"],
            "material_area_changes": sum(
                bool(report.get("material_area_change")) for report in feature_reports
            ),
            "needs_review": needs_review,
        },
        "features": feature_reports,
    }
    return sanitized, report


def write_geometry_qa_report(
    path: Path,
    report: Mapping[str, Any],
    *,
    context: str,
) -> dict[str, Any]:
    """Append a sanitation pass while retaining earlier vectorization diagnostics."""
    if path.is_file():
        payload = read_json(path)
    else:
        payload = {
            "generated_at": utc_now_iso(),
            "status": "passed",
            "summary": {
                "needs_review": False,
                "unrecoverable": 0,
                "material_area_changes": 0,
            },
            "passes": [],
        }
    pass_payload = copy.deepcopy(dict(report))
    pass_payload["context"] = context
    payload["passes"] = [
        value
        for value in payload.setdefault("passes", [])
        if value.get("context") != context
    ]
    payload["passes"].append(pass_payload)
    payload["generated_at"] = utc_now_iso()
    payload["summary"] = {
        "needs_review": any(
            value.get("summary", {}).get("needs_review", False)
            for value in payload["passes"]
        ),
        "unrecoverable": sum(
            int(value.get("summary", {}).get("unrecoverable", 0))
            for value in payload["passes"]
        ),
        "material_area_changes": sum(
            int(value.get("summary", {}).get("material_area_changes", 0))
            for value in payload["passes"]
        ),
        "latest_input_feature_count": report.get("summary", {}).get(
            "input_feature_count"
        ),
        "latest_output_feature_count": report.get("summary", {}).get(
            "output_feature_count"
        ),
        "latest_polygonal_component_count": report.get("summary", {}).get(
            "polygonal_component_count"
        ),
    }
    payload["status"] = (
        "needs_review" if payload["summary"]["needs_review"] else "passed"
    )
    write_json(path, payload)
    return payload
