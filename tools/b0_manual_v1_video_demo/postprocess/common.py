"""Shared I/O, geometry, and numeric helpers for post-processing."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


DEMO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CORE_SRC = PROJECT_ROOT / "src"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

EARTH_RADIUS_M = 6_378_137.0


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def write_csv(path: Path, rows: Iterable[Mapping[str, Any]], fieldnames: Sequence[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)
    return path


def sha256_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def ffprobe_video(path: Path) -> dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate,nb_frames,duration:format=duration,size:format_tags",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def matrix_to_list(matrix: np.ndarray) -> list[list[float]]:
    return [[float(value) for value in row] for row in matrix]


def list_to_matrix(value: Sequence[Sequence[float]]) -> np.ndarray:
    matrix = np.asarray(value, dtype=np.float64)
    if matrix.shape != (3, 3):
        raise ValueError(f"Expected 3x3 matrix, got {matrix.shape}")
    return matrix


def transform_points(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    array = np.asarray(points, dtype=np.float64)
    if array.size == 0:
        return np.empty((0, 2), dtype=np.float64)
    homogeneous = np.column_stack([array, np.ones(len(array), dtype=np.float64)])
    warped = homogeneous @ matrix.T
    divisor = warped[:, 2:3]
    if np.any(np.abs(divisor) < 1e-12):
        raise ValueError("Transform maps a point to infinity")
    return warped[:, :2] / divisor


def frame_corners(width: int, height: int, roi_height: int | None = None) -> np.ndarray:
    effective_height = height if roi_height is None else min(height, roi_height)
    return np.asarray(
        [[0.0, 0.0], [float(width), 0.0], [float(width), float(effective_height)], [0.0, float(effective_height)]],
        dtype=np.float64,
    )


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    sorted_values = values[order]
    sorted_weights = weights[order]
    cutoff = 0.5 * float(sorted_weights.sum())
    index = int(np.searchsorted(np.cumsum(sorted_weights), cutoff, side="left"))
    return float(sorted_values[min(index, len(sorted_values) - 1)])


def quantiles(values: Sequence[float] | np.ndarray) -> dict[str, float | None]:
    array = np.asarray(values, dtype=np.float64)
    array = array[np.isfinite(array)]
    if array.size == 0:
        return {key: None for key in ("min", "p05", "p25", "p50", "p75", "p95", "max")}
    result = np.quantile(array, [0.0, 0.05, 0.25, 0.5, 0.75, 0.95, 1.0])
    return {
        key: float(value)
        for key, value in zip(("min", "p05", "p25", "p50", "p75", "p95", "max"), result)
    }


def diagonal_fov_to_axes(diagonal_fov_deg: float, width: int, height: int) -> tuple[float, float]:
    """Convert diagonal FOV to horizontal/vertical FOV under a pinhole assumption."""
    if not 0 < diagonal_fov_deg < 180 or width <= 0 or height <= 0:
        raise ValueError("Invalid FOV or image dimensions")
    aspect = width / height
    diagonal_tangent = math.tan(math.radians(diagonal_fov_deg) / 2.0)
    vertical_tangent = diagonal_tangent / math.sqrt(aspect * aspect + 1.0)
    horizontal_tangent = aspect * vertical_tangent
    return (
        math.degrees(2.0 * math.atan(horizontal_tangent)),
        math.degrees(2.0 * math.atan(vertical_tangent)),
    )


def latlon_to_local_enu(
    latitude: np.ndarray | Sequence[float],
    longitude: np.ndarray | Sequence[float],
    origin_latitude: float,
    origin_longitude: float,
) -> np.ndarray:
    lat = np.radians(np.asarray(latitude, dtype=np.float64))
    lon = np.radians(np.asarray(longitude, dtype=np.float64))
    lat0 = math.radians(origin_latitude)
    lon0 = math.radians(origin_longitude)
    east = (lon - lon0) * EARTH_RADIUS_M * math.cos(lat0)
    north = (lat - lat0) * EARTH_RADIUS_M
    return np.column_stack([east, north])


def local_enu_to_latlon(
    east: np.ndarray | Sequence[float],
    north: np.ndarray | Sequence[float],
    origin_latitude: float,
    origin_longitude: float,
) -> np.ndarray:
    e = np.asarray(east, dtype=np.float64)
    n = np.asarray(north, dtype=np.float64)
    lat0 = math.radians(origin_latitude)
    latitude = origin_latitude + np.degrees(n / EARTH_RADIUS_M)
    longitude = origin_longitude + np.degrees(e / (EARTH_RADIUS_M * math.cos(lat0)))
    return np.column_stack([longitude, latitude])


def point_in_polygon(point: Sequence[float], polygon: Sequence[Sequence[float]]) -> bool:
    x, y = float(point[0]), float(point[1])
    inside = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            crossing_x = (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-15) + x1
            if x < crossing_x:
                inside = not inside
    return inside


def point_in_polygon_geometry(
    point: Sequence[float],
    polygon_or_geometry: Sequence[Sequence[float]] | Mapping[str, Any],
) -> bool:
    """Point-in-area for a legacy exterior ring or GeoJSON Polygon with holes."""
    if not isinstance(polygon_or_geometry, Mapping):
        return point_in_polygon(point, polygon_or_geometry)
    geometry_type = polygon_or_geometry.get("type")
    coordinates = polygon_or_geometry.get("coordinates", [])
    polygons = coordinates if geometry_type == "MultiPolygon" else [coordinates]
    if geometry_type not in {"Polygon", "MultiPolygon"}:
        return False
    for polygon in polygons:
        if not polygon or not point_in_polygon(point, polygon[0]):
            continue
        if any(point_in_polygon(point, hole) for hole in polygon[1:]):
            continue
        return True
    return False


def polygon_area(polygon: Sequence[Sequence[float]]) -> float:
    total = 0.0
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(index + 1) % len(polygon)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def distance_to_polygon_boundary(point: Sequence[float], polygon: Sequence[Sequence[float]]) -> float:
    p = np.asarray(point, dtype=np.float64)
    distances = []
    for index, start in enumerate(polygon):
        a = np.asarray(start, dtype=np.float64)
        b = np.asarray(polygon[(index + 1) % len(polygon)], dtype=np.float64)
        delta = b - a
        length_squared = float(delta @ delta)
        fraction = 0.0 if length_squared == 0 else float(np.clip(((p - a) @ delta) / length_squared, 0, 1))
        distances.append(float(np.linalg.norm(p - (a + fraction * delta))))
    return min(distances, default=float("inf"))


def distance_to_polygon_geometry_boundary(
    point: Sequence[float],
    polygon_or_geometry: Sequence[Sequence[float]] | Mapping[str, Any],
) -> float:
    """Distance to the closest exterior or hole boundary."""
    if not isinstance(polygon_or_geometry, Mapping):
        return distance_to_polygon_boundary(point, polygon_or_geometry)
    geometry_type = polygon_or_geometry.get("type")
    coordinates = polygon_or_geometry.get("coordinates", [])
    polygons = coordinates if geometry_type == "MultiPolygon" else [coordinates]
    if geometry_type not in {"Polygon", "MultiPolygon"}:
        return math.inf
    distances = [
        distance_to_polygon_boundary(point, ring)
        for polygon in polygons
        for ring in polygon
        if len(ring) >= 3
    ]
    return min(distances) if distances else math.inf


def _orientation(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> float:
    return (float(b[1]) - float(a[1])) * (float(c[0]) - float(b[0])) - (
        float(b[0]) - float(a[0])
    ) * (float(c[1]) - float(b[1]))


def _segments_intersect(
    a: Sequence[float], b: Sequence[float], c: Sequence[float], d: Sequence[float]
) -> bool:
    o1, o2 = _orientation(a, b, c), _orientation(a, b, d)
    o3, o4 = _orientation(c, d, a), _orientation(c, d, b)
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def polygon_self_intersects(polygon: Sequence[Sequence[float]]) -> bool:
    count = len(polygon)
    if count < 4:
        return False
    for first in range(count):
        a, b = polygon[first], polygon[(first + 1) % count]
        for second in range(first + 1, count):
            if second in {first, (first + 1) % count} or (second + 1) % count == first:
                continue
            c, d = polygon[second], polygon[(second + 1) % count]
            if _segments_intersect(a, b, c, d):
                return True
    return False


def confidence_level(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"
