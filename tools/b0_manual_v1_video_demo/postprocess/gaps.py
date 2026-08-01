"""Robust row reconstruction, expected slots, missing points, and gap grouping."""

from __future__ import annotations

import logging
import math
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks
from scipy.spatial import cKDTree

from gap_plot.data.schemas import ExpectedPlantingPoint, GapResult, MissingPlantingPoint, PixelPoint, PlantDetection
from gap_plot.fusion.temporal import TemporalFusion
from gap_plot.gap_analysis.clusterer import GapClusterer
from gap_plot.gap_analysis.expected_points import ExpectedPointGenerator
from gap_plot.gap_analysis.missing_points import MissingPointDetector
from gap_plot.geometry.rows import ReconstructedRow, RowReconstructionResult, RowReconstructor

from .common import (
    confidence_level,
    distance_to_polygon_geometry_boundary,
    list_to_matrix,
    point_in_polygon_geometry,
    quantiles,
    read_json,
    transform_points,
    write_csv,
    write_json,
)
from .geometry_sanitize import iter_polygon_rings, validate_polygonal_geometry


LOGGER = logging.getLogger(__name__)


def estimate_dominant_orientation(points: np.ndarray, bin_degrees: float = 2.0) -> tuple[float, float]:
    points = np.asarray(points, dtype=np.float64)
    if len(points) < 4:
        raise ValueError("At least four plants are required for row orientation")
    distances, indices = cKDTree(points).query(points, k=min(5, len(points)))
    vectors = []
    weights = []
    for source_index in range(len(points)):
        for rank in range(1, indices.shape[1]):
            target_index = int(indices[source_index, rank])
            vector = points[target_index] - points[source_index]
            distance = float(distances[source_index, rank])
            if distance > 0:
                vectors.append(vector)
                weights.append(1.0 / distance)
    vectors_array = np.asarray(vectors)
    angles = np.mod(np.degrees(np.arctan2(vectors_array[:, 1], vectors_array[:, 0])), 180.0)
    bins = np.arange(0.0, 180.0 + bin_degrees, bin_degrees)
    histogram, edges = np.histogram(angles, bins=bins, weights=np.asarray(weights))
    peak = int(np.argmax(histogram))
    center = (edges[peak] + edges[peak + 1]) / 2.0
    delta = np.abs(((angles - center + 90.0) % 180.0) - 90.0)
    near = delta <= max(3.0, 1.5 * bin_degrees)
    orientation = float(np.mod(np.degrees(np.angle(np.sum(np.asarray(weights)[near] * np.exp(2j * np.radians(angles[near]))))) / 2.0, 180.0))
    confidence = float(histogram[peak] / max(histogram.sum(), 1e-9))
    return orientation, min(1.0, confidence * 6.0)


def robust_spacing(values: Sequence[float]) -> tuple[float, float]:
    array = np.asarray(values, dtype=np.float64)
    array = array[np.isfinite(array) & (array > 0)]
    if len(array) < 3:
        raise ValueError("Insufficient spacing samples")
    upper = float(np.quantile(array, 0.65))
    base = array[array <= upper]
    spacing = float(np.median(base))
    mad = float(np.median(np.abs(base - spacing)))
    confidence = float(np.clip(1.0 - (1.4826 * mad / max(spacing, 1e-9)), 0.0, 1.0))
    return spacing, confidence


class RobustRowReconstructor(RowReconstructor):
    def __init__(self, minimum_plants_per_row: int = 5, row_band_fraction: float = 0.42, angle_bin_deg: float = 2.0) -> None:
        self.minimum_plants_per_row = minimum_plants_per_row
        self.row_band_fraction = row_band_fraction
        self.angle_bin_deg = angle_bin_deg

    def reconstruct(self, frame_id: str, plants: Sequence[PlantDetection]) -> RowReconstructionResult:
        points = np.asarray([[plant.center.x, plant.center.y] for plant in plants], dtype=np.float64)
        orientation, orientation_confidence = estimate_dominant_orientation(points, self.angle_bin_deg)
        angle = math.radians(orientation)
        along = points @ np.asarray([math.cos(angle), math.sin(angle)])
        across = points @ np.asarray([-math.sin(angle), math.cos(angle)])
        nearest = cKDTree(points).query(points, k=2)[0][:, 1]
        normal_spacing = float(np.median(nearest))
        # Estimate physical row spacing from neighbors that are nearly side-by-side in the
        # along-row direction. This avoids treating the much smaller plant spacing as lane spacing.
        neighbor_count = min(16, len(points))
        _, neighbor_indices = cKDTree(points).query(points, k=neighbor_count)
        cross_neighbor_distances = []
        for source_index in range(len(points)):
            for target_index in neighbor_indices[source_index, 1:]:
                along_delta = abs(float(along[int(target_index)] - along[source_index]))
                across_delta = abs(float(across[int(target_index)] - across[source_index]))
                if (
                    along_delta < 0.55 * normal_spacing
                    and 1.5 * normal_spacing < across_delta < 4.5 * normal_spacing
                ):
                    cross_neighbor_distances.append(across_delta)
        if len(cross_neighbor_distances) < self.minimum_plants_per_row:
            preliminary_inter_row = 2.6 * normal_spacing
        else:
            cross_bins = np.arange(1.5 * normal_spacing, 4.5 * normal_spacing + 1.0, 1.0)
            cross_histogram, cross_edges = np.histogram(cross_neighbor_distances, bins=cross_bins)
            cross_smoothed = gaussian_filter1d(cross_histogram.astype(np.float64), max(1.0, 0.12 * normal_spacing))
            cross_peaks, _ = find_peaks(
                cross_smoothed,
                distance=max(3, int(round(1.2 * normal_spacing))),
                prominence=max(1.0, float(np.quantile(cross_smoothed, 0.50)) * 0.10),
            )
            preliminary_inter_row = (
                float((cross_edges[cross_peaks[0]] + cross_edges[cross_peaks[0] + 1]) / 2.0)
                if len(cross_peaks)
                else 2.6 * normal_spacing
            )

        # Estimate the slow common row curvature from the phase of the repeated row lattice.
        # A median field center is biased whenever polygon width changes, while the circular
        # lattice phase remains stable when rows enter or leave the surveyed area.
        along_span = float(np.ptp(along))
        phase_bin_count = max(7, min(35, int(along_span / max(10.0 * normal_spacing, 1.0))))
        phase_edges = np.linspace(float(along.min()), float(along.max()), phase_bin_count + 1)
        phase_along = []
        phase_values = []
        for start, end in zip(phase_edges[:-1], phase_edges[1:]):
            members = (along >= start) & (along <= end)
            if int(members.sum()) < self.minimum_plants_per_row:
                continue
            circular = np.mean(np.exp(2j * np.pi * across[members] / preliminary_inter_row))
            phase_along.append(float((start + end) / 2.0))
            phase_values.append(float(np.angle(circular) * preliminary_inter_row / (2.0 * np.pi)))
        if len(phase_values) < 2:
            phase_along = [float(along.min()), float(along.max())]
            phase_values = [0.0, 0.0]
        unwrapped_phase = (
            np.unwrap(np.asarray(phase_values) * 2.0 * np.pi / preliminary_inter_row)
            * preliminary_inter_row
            / (2.0 * np.pi)
        )
        smoothed_phase = gaussian_filter1d(unwrapped_phase, 1.0) if len(unwrapped_phase) >= 3 else unwrapped_phase
        fitted_drift = np.interp(along, np.asarray(phase_along), smoothed_phase)
        drift_reference = float(np.median(fitted_drift))
        corrected_across = across - fitted_drift + drift_reference
        # Physical rows appear as density peaks across the dominant direction. Sequential
        # threshold clustering over-splits one row when registered observations have lateral jitter.
        peak_padding = max(10, int(round(1.5 * normal_spacing)))
        lower = int(math.floor(float(corrected_across.min()))) - peak_padding
        upper = int(math.ceil(float(corrected_across.max()))) + peak_padding
        bins = np.arange(lower, upper + 1, dtype=np.float64)
        histogram, edges = np.histogram(corrected_across, bins=bins)
        smoothing_sigma = max(1.5, 0.18 * normal_spacing)
        smoothed = gaussian_filter1d(histogram.astype(np.float64), smoothing_sigma)
        minimum_peak_distance = max(3, int(round(0.65 * preliminary_inter_row)))
        prominence = max(0.25, float(np.quantile(smoothed, 0.60)) * 0.15)
        peak_indices, peak_properties = find_peaks(
            smoothed,
            distance=minimum_peak_distance,
            prominence=prominence,
        )
        if len(peak_indices) == 0:
            raise ValueError("Cross-row density profile has no stable row peaks")
        peak_centers = (edges[peak_indices] + edges[peak_indices + 1]) / 2.0
        distances_to_peaks = np.abs(corrected_across[:, None] - peak_centers[None, :])
        nearest_peaks = np.argmin(distances_to_peaks, axis=1)
        nearest_peak_residuals = distances_to_peaks[np.arange(len(points)), nearest_peaks]
        assignment_band = max(
            2.0,
            preliminary_inter_row * self.row_band_fraction,
            float(np.quantile(nearest_peak_residuals, 0.995)),
        )
        lanes = [
            np.flatnonzero(
                (nearest_peaks == peak_index)
                & (distances_to_peaks[:, peak_index] <= assignment_band)
            ).astype(int).tolist()
            for peak_index in range(len(peak_centers))
        ]
        lanes = [lane for lane in lanes if len(lane) >= self.minimum_plants_per_row]
        lane_centers = [float(np.median(corrected_across[lane])) for lane in lanes]
        inter_row = float(np.median(np.diff(lane_centers))) if len(lane_centers) > 1 else None
        rows = []
        spacing_samples = []
        for lane_number, lane in enumerate(lanes, start=1):
            sorted_lane = sorted(lane, key=lambda index: along[index])
            diffs = np.diff(along[sorted_lane])
            spacing_samples.extend(diffs[diffs > 0].tolist())
            try:
                spacing, spacing_confidence = robust_spacing(diffs)
            except ValueError:
                spacing, spacing_confidence = None, 0.0
            rows.append(ReconstructedRow(
                row_id=f"{frame_id}_row_{lane_number:04d}",
                plant_ids=tuple(plants[index].plant_id for index in sorted_lane),
                centerline=tuple(PixelPoint(float(points[index, 0]), float(points[index, 1])) for index in sorted_lane),
                median_spacing_px=spacing,
                confidence=float(np.clip(0.5 * orientation_confidence + 0.5 * spacing_confidence, 0, 1)),
                method="nearest_vector_orientation_then_robust_across_row_bands",
            ))
        diagnostics = {
            "plant_count": len(plants),
            "candidate_lane_count": len(lanes),
            "orientation_confidence": orientation_confidence,
            "normal_nearest_spacing_px": normal_spacing,
            "cross_neighbor_distance_px": quantiles(cross_neighbor_distances),
            "preliminary_inter_row_spacing_px": preliminary_inter_row,
            "common_cross_row_phase_bin_count": len(phase_values),
            "common_cross_row_unwrapped_phase_px": quantiles(unwrapped_phase),
            "common_cross_row_drift_range_px": float(np.ptp(fitted_drift)),
            "row_density_smoothing_sigma_px": smoothing_sigma,
            "row_peak_minimum_distance_px": minimum_peak_distance,
            "row_peak_prominence": prominence,
            "row_peak_count_before_minimum_plant_gate": len(peak_indices),
            "row_peak_prominence_distribution": quantiles(peak_properties.get("prominences", [])),
            "row_assignment_band_px": assignment_band,
            "row_assignment_residual_px": quantiles(nearest_peak_residuals),
            "all_along_row_spacing_samples": spacing_samples,
        }
        return RowReconstructionResult(frame_id, tuple(rows), orientation, inter_row, diagnostics)


class RobustExpectedPointGenerator(ExpectedPointGenerator):
    def generate(self, row_result: RowReconstructionResult, area_result: Any = None) -> Sequence[ExpectedPlantingPoint]:
        del area_result
        expected = []
        for row in row_result.rows:
            if row.median_spacing_px is None or len(row.centerline) < 2:
                continue
            slot = 0
            for pair_index in range(len(row.centerline) - 1):
                start = np.asarray([row.centerline[pair_index].x, row.centerline[pair_index].y])
                end = np.asarray([row.centerline[pair_index + 1].x, row.centerline[pair_index + 1].y])
                distance = float(np.linalg.norm(end - start))
                intervals = max(1, int(round(distance / row.median_spacing_px)))
                if pair_index == 0:
                    expected.append(ExpectedPlantingPoint(f"{row.row_id}_slot_{slot:05d}", row_result.frame_id, row.row_id, slot, PixelPoint(float(start[0]), float(start[1])), row.confidence, row.confidence))
                for step in range(1, intervals + 1):
                    slot += 1
                    point = start + (end - start) * (step / intervals)
                    expected.append(ExpectedPlantingPoint(f"{row.row_id}_slot_{slot:05d}", row_result.frame_id, row.row_id, slot, PixelPoint(float(point[0]), float(point[1])), row.confidence, row.confidence))
        return expected


def split_rows_at_polygon_boundary(
    row_result: RowReconstructionResult,
    polygon: Sequence[Sequence[float]] | Mapping[str, Any],
    minimum_plants_per_segment: int,
) -> tuple[RowReconstructionResult, dict[str, int]]:
    """Split lanes wherever a segment exits an exterior ring or enters a hole."""
    segmented_rows: list[ReconstructedRow] = []
    cut_count = 0
    discarded_short_fragments = 0
    for row in row_result.rows:
        fragments: list[tuple[list[str], list[PixelPoint]]] = []
        current_ids = [row.plant_ids[0]]
        current_points = [row.centerline[0]]
        for plant_id, point in zip(row.plant_ids[1:], row.centerline[1:]):
            start = current_points[-1]
            samples = np.linspace(
                np.asarray([start.x, start.y]),
                np.asarray([point.x, point.y]),
                21,
            )
            if all(point_in_polygon_geometry(sample, polygon) for sample in samples):
                current_ids.append(plant_id)
                current_points.append(point)
                continue
            cut_count += 1
            fragments.append((current_ids, current_points))
            current_ids = [plant_id]
            current_points = [point]
        fragments.append((current_ids, current_points))
        valid_fragments = [fragment for fragment in fragments if len(fragment[0]) >= minimum_plants_per_segment]
        discarded_short_fragments += len(fragments) - len(valid_fragments)
        if len(valid_fragments) == 1 and len(fragments) == 1:
            segmented_rows.append(row)
            continue
        for segment_index, (plant_ids, centerline) in enumerate(valid_fragments, start=1):
            distances = [
                math.hypot(second.x - first.x, second.y - first.y)
                for first, second in zip(centerline, centerline[1:])
            ]
            try:
                spacing, spacing_confidence = robust_spacing(distances)
            except ValueError:
                spacing, spacing_confidence = row.median_spacing_px, row.confidence
            segmented_rows.append(ReconstructedRow(
                row_id=f"{row.row_id}_segment_{segment_index:02d}",
                plant_ids=tuple(plant_ids),
                centerline=tuple(centerline),
                median_spacing_px=spacing,
                confidence=float(min(row.confidence, spacing_confidence)),
                method=f"{row.method}+polygon_internal_segment_split",
            ))
    diagnostics = dict(row_result.diagnostics)
    diagnostics.update({
        "physical_row_count_before_polygon_split": len(row_result.rows),
        "internal_row_segment_count": len(segmented_rows),
        "polygon_boundary_segment_cut_count": cut_count,
        "short_row_fragments_discarded": discarded_short_fragments,
    })
    return (
        RowReconstructionResult(
            row_result.frame_id,
            tuple(segmented_rows),
            row_result.dominant_orientation_deg,
            row_result.inter_row_spacing_px,
            diagnostics,
        ),
        {
            "physical_rows": len(row_result.rows),
            "row_segments": len(segmented_rows),
            "segment_cuts": cut_count,
            "short_fragments_discarded": discarded_short_fragments,
        },
    )


class NearestMissingPointDetector(MissingPointDetector):
    def __init__(self, tolerance_fraction: float = 0.42) -> None:
        self.tolerance_fraction = tolerance_fraction

    def detect(self, expected_points: Sequence[ExpectedPlantingPoint], plants: Sequence[PlantDetection], area_result: Any = None) -> Sequence[MissingPlantingPoint]:
        del area_result
        if not expected_points or not plants:
            return []
        plant_points = np.asarray([[plant.center.x, plant.center.y] for plant in plants])
        tree = cKDTree(plant_points)
        by_row: dict[str, list[ExpectedPlantingPoint]] = {}
        for expected in expected_points:
            by_row.setdefault(expected.row_id, []).append(expected)
        missing = []
        for row_points in by_row.values():
            distances_between = [np.linalg.norm(np.asarray([a.center.x, a.center.y]) - np.asarray([b.center.x, b.center.y])) for a, b in zip(row_points, row_points[1:])]
            spacing = float(np.median(distances_between)) if distances_between else 1.0
            for expected in row_points:
                distance, _ = tree.query([expected.center.x, expected.center.y])
                if float(distance) > self.tolerance_fraction * spacing:
                    confidence = float(np.clip(0.5 * expected.row_confidence + 0.5 * expected.spacing_confidence, 0, 1))
                    missing.append(MissingPlantingPoint(f"missing_{expected.point_id}", expected.point_id, expected.frame_id, expected.center, confidence, ("no_unique_plant_within_robust_spacing_tolerance",)))
        return missing


class AdjacentMissingGapClusterer(GapClusterer):
    def cluster(self, missing_points: Sequence[MissingPlantingPoint]) -> Sequence[GapResult]:
        groups: dict[str, list[tuple[int, MissingPlantingPoint]]] = {}
        for missing in missing_points:
            prefix, slot_text = missing.expected_point_id.rsplit("_slot_", 1)
            groups.setdefault(prefix, []).append((int(slot_text), missing))
        gaps = []
        gap_number = 0
        for row_id, values in groups.items():
            ordered = sorted(values)
            runs: list[list[tuple[int, MissingPlantingPoint]]] = []
            for item in ordered:
                if not runs or item[0] != runs[-1][-1][0] + 1:
                    runs.append([item])
                else:
                    runs[-1].append(item)
            for run in runs:
                gap_number += 1
                points = np.asarray([[item.center.x, item.center.y] for _, item in run])
                center = points.mean(axis=0)
                half = 4.0
                polygon = tuple(PixelPoint(float(x), float(y)) for x, y in [[center[0]-half, center[1]-half], [center[0]+half, center[1]-half], [center[0]+half, center[1]+half], [center[0]-half, center[1]+half]])
                confidence = float(np.mean([item.confidence for _, item in run]))
                gaps.append(GapResult(f"gap_{gap_number:06d}", tuple(), polygon, None, PixelPoint(float(center[0]), float(center[1])), None, len(run), None, None, None, confidence, confidence_level(confidence), None, {"row_id": row_id}, tuple(item.missing_id for _, item in run), tuple()))
        return gaps


class MapLevelGapFusion(TemporalFusion):
    """Protocol adapter: inputs are already map-level, so no temporal pixel fusion remains."""

    def fuse(self, gaps: Sequence[GapResult]) -> Sequence[GapResult]:
        return tuple(gaps)


def _coverage_at(points: np.ndarray, coverage: np.ndarray, local_to_preview: np.ndarray) -> np.ndarray:
    preview = np.rint(transform_points(points, local_to_preview)).astype(int)
    result = np.zeros(len(points), dtype=np.int32)
    valid = (preview[:, 0] >= 0) & (preview[:, 0] < coverage.shape[1]) & (preview[:, 1] >= 0) & (preview[:, 1] < coverage.shape[0])
    result[valid] = coverage[preview[valid, 1], preview[valid, 0]]
    return result


def analyze_gaps(postprocess_run: Path, plots_path: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    postprocess_run = postprocess_run.resolve()
    plots = read_json(plots_path.resolve())
    if not plots.get("features"):
        raise RuntimeError("No real manual polygon exists. Run plot-editor and save plots_local.geojson first; TEST_PLOT is never used as a real result.")
    for feature in plots["features"]:
        geometry = feature["geometry"]
        valid, reason = validate_polygonal_geometry(geometry)
        if not valid:
            raise ValueError(
                f"Invalid polygon {feature['properties'].get('plot_id')}: {reason}"
            )
    unique = pd.read_csv(postprocess_run / "unique_plants.csv")
    mosaic_transform = read_json(postprocess_run / "mosaic_transform.json")
    local_to_preview = list_to_matrix(mosaic_transform["local_to_preview"])
    coverage = np.load(postprocess_run / "coverage" / "coverage_counts.npz")["coverage"]
    gap_cfg = config["gap_analysis"]
    reconstructor = RobustRowReconstructor(int(gap_cfg["minimum_plants_per_row"]), float(gap_cfg["row_assignment_band_spacing_fraction"]), float(gap_cfg["row_angle_bin_deg"]))
    generator = RobustExpectedPointGenerator()
    detector = NearestMissingPointDetector(float(gap_cfg["missing_match_spacing_fraction"]))
    clusterer = AdjacentMissingGapClusterer()
    fusion: TemporalFusion = MapLevelGapFusion()

    row_features, expected_rows, missing_rows, gap_rows, gap_features, plot_rows = [], [], [], [], [], []
    rejected_rows: list[dict[str, Any]] = []
    # Re-analysis must not retain assignments from an earlier polygon version.
    unique["plot_id"] = ""
    rejected_boundary = rejected_coverage = rejected_outside_polygon = 0
    downgraded_near_suspicious = suppressed_by_low_support = 0
    global_gap_index = 0
    for feature in plots["features"]:
        plot_id = str(feature["properties"]["plot_id"])
        polygon = feature["geometry"]
        inside = np.asarray(
            [
                point_in_polygon_geometry((row.local_x, row.local_y), polygon)
                for row in unique.itertuples()
            ]
        )
        unique.loc[inside, "plot_id"] = plot_id
        confirmed_selected = unique[inside & (unique["support_status"] == "confirmed")]
        low_support_selected = unique[inside & (unique["support_status"] == "low_support")]
        suspicious_selected = unique[inside & (unique["support_status"] == "suspicious_singleton")]
        selected = pd.concat([confirmed_selected, low_support_selected], ignore_index=False)
        if len(selected) < int(gap_cfg["minimum_plants_per_plot"]):
            plot_rows.append({"plot_id": plot_id, "analysis_status": "insufficient_unique_plants", "confirmed_unique_plants": len(confirmed_selected), "low_support_unique_plants": len(low_support_selected), "unique_detected_plants": len(selected), "row_count": 0, "dominant_row_orientation_deg": "", "along_row_spacing_local_px": "", "cross_row_spacing_local_px": "", "expected_capacity": 0, "estimated_missing_plants": 0, "gap_count": 0, "missing_rate": "", "expected_rejected_outside_polygon": 0, "candidate_missing_before_gates": 0, "rejected_boundary": 0, "rejected_coverage": 0, "downgraded_near_suspicious": 0, "suppressed_by_low_support": 0})
            continue
        plants = [PlantDetection(str(row.plant_id), plot_id, "map", PixelPoint(float(row.local_x), float(row.local_y)), float(row.mean_confidence), model_version="b0_manual_v1") for row in selected.itertuples()]
        raw_row_result = reconstructor.reconstruct(plot_id, plants)
        row_result, row_split = split_rows_at_polygon_boundary(
            raw_row_result,
            polygon,
            int(gap_cfg["minimum_plants_per_row"]),
        )
        expected = list(generator.generate(row_result))
        expected_before_polygon_gate = len(expected)
        expected = [
            item
            for item in expected
            if point_in_polygon_geometry((item.center.x, item.center.y), polygon)
        ]
        plot_rejected_outside = expected_before_polygon_gate - len(expected)
        rejected_outside_polygon += plot_rejected_outside
        expected_by_id = {item.point_id: item for item in expected}
        row_spacing = {row.row_id: float(row.median_spacing_px or 1.0) for row in row_result.rows}
        enriched_expected = []
        for item in expected:
            boundary = distance_to_polygon_geometry_boundary(
                (item.center.x, item.center.y), polygon
            )
            enriched_expected.append(replace(item, boundary_distance_px=boundary))
        expected = enriched_expected
        candidate_missing = detector.detect(expected, plants)
        confirmed_plants = [PlantDetection(str(row.plant_id), plot_id, "map", PixelPoint(float(row.local_x), float(row.local_y)), float(row.mean_confidence), model_version="b0_manual_v1") for row in confirmed_selected.itertuples()]
        confirmed_only_missing_ids = {item.expected_point_id for item in detector.detect(expected, confirmed_plants)}
        selected_missing_ids = {item.expected_point_id for item in candidate_missing}
        plot_suppressed_low_support = len(confirmed_only_missing_ids - selected_missing_ids)
        suppressed_by_low_support += plot_suppressed_low_support
        accepted_missing = []
        missing_coverage: dict[str, int] = {}
        missing_near_suspicious: dict[str, int] = {}
        suspicious_tree = cKDTree(suspicious_selected[["local_x", "local_y"]].to_numpy(dtype=np.float64)) if len(suspicious_selected) else None
        plot_rejected_boundary = plot_rejected_coverage = plot_downgraded = 0
        for item in candidate_missing:
            expected_item = expected_by_id[item.expected_point_id]
            spacing = row_spacing[expected_item.row_id]
            boundary = distance_to_polygon_geometry_boundary(
                (item.center.x, item.center.y), polygon
            )
            coverage_count = int(_coverage_at(np.asarray([[item.center.x, item.center.y]]), coverage, local_to_preview)[0])
            if boundary < float(gap_cfg["boundary_buffer_row_spacing_fraction"]) * spacing:
                rejected_boundary += 1
                plot_rejected_boundary += 1
                rejected_rows.append({
                    "candidate_id": f"rejected_{item.missing_id}",
                    "plot_id": plot_id,
                    "row_id": expected_item.row_id,
                    "slot_index": expected_item.slot_index,
                    "local_x": item.center.x,
                    "local_y": item.center.y,
                    "rejection_reason": "polygon_boundary_exclusion_buffer",
                    "boundary_distance_local_px": boundary,
                    "row_spacing_local_px": spacing,
                    "boundary_distance_spacing_fraction": boundary / max(spacing, 1e-9),
                    "coverage_count": coverage_count,
                })
                continue
            if coverage_count < int(gap_cfg["minimum_coverage_opportunity"]):
                rejected_coverage += 1
                plot_rejected_coverage += 1
                rejected_rows.append({
                    "candidate_id": f"rejected_{item.missing_id}",
                    "plot_id": plot_id,
                    "row_id": expected_item.row_id,
                    "slot_index": expected_item.slot_index,
                    "local_x": item.center.x,
                    "local_y": item.center.y,
                    "rejection_reason": "insufficient_visibility_opportunity",
                    "boundary_distance_local_px": boundary,
                    "row_spacing_local_px": spacing,
                    "boundary_distance_spacing_fraction": boundary / max(spacing, 1e-9),
                    "coverage_count": coverage_count,
                })
                continue
            nearby_suspicious = 0
            adjusted_item = item
            if suspicious_tree is not None:
                nearby = suspicious_tree.query_ball_point(
                    [item.center.x, item.center.y],
                    r=float(gap_cfg["missing_match_spacing_fraction"]) * spacing,
                )
                nearby_suspicious = len(nearby)
            reasons = item.reasons + (f"coverage_opportunity={coverage_count}", "internal_slot_boundary_gate_passed")
            if nearby_suspicious:
                adjusted_item = replace(
                    item,
                    confidence=float(item.confidence * 0.5),
                    reasons=reasons + (f"nearby_suspicious_singletons={nearby_suspicious}", "confidence_downgraded_for_low_support_evidence"),
                )
                downgraded_near_suspicious += 1
                plot_downgraded += 1
            else:
                adjusted_item = replace(item, reasons=reasons)
            accepted_missing.append(adjusted_item)
            missing_coverage[item.missing_id] = coverage_count
            missing_near_suspicious[item.missing_id] = nearby_suspicious
        gaps = list(fusion.fuse(clusterer.cluster(accepted_missing)))

        for row in row_result.rows:
            coords = [[point.x, point.y] for point in row.centerline]
            row_features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": {"plot_id": plot_id, "row_id": row.row_id, "plant_count": len(row.plant_ids), "median_spacing_local_px": row.median_spacing_px, "inter_row_spacing_local_px": row_result.inter_row_spacing_px, "dominant_orientation_deg": row_result.dominant_orientation_deg, "confidence": row.confidence, "method": row.method}})
        missing_ids = {item.missing_id: item for item in accepted_missing}
        for item in expected:
            expected_rows.append({"point_id": item.point_id, "plot_id": plot_id, "row_id": item.row_id, "slot_index": item.slot_index, "local_x": item.center.x, "local_y": item.center.y, "row_confidence": item.row_confidence, "spacing_confidence": item.spacing_confidence, "boundary_distance_local_px": distance_to_polygon_geometry_boundary((item.center.x, item.center.y), polygon), "is_missing": f"missing_{item.point_id}" in missing_ids})
        for item in accepted_missing:
            expected_item = next(value for value in expected if value.point_id == item.expected_point_id)
            missing_rows.append({"missing_id": item.missing_id, "expected_point_id": item.expected_point_id, "plot_id": plot_id, "row_id": expected_item.row_id, "slot_index": expected_item.slot_index, "local_x": item.center.x, "local_y": item.center.y, "coverage_count": missing_coverage[item.missing_id], "nearby_suspicious_count": missing_near_suspicious[item.missing_id], "confidence": item.confidence, "evidence_status": "review_near_suspicious_support" if missing_near_suspicious[item.missing_id] else "candidate_visual_review", "reasons": "|".join(item.reasons)})
        for gap in gaps:
            global_gap_index += 1
            row_id = str(gap.model_versions["row_id"])
            member_expected = [expected_by_id[missing_ids[mid].expected_point_id] for mid in gap.missing_point_ids]
            slots = [item.slot_index for item in member_expected]
            coverages = [missing_coverage[mid] for mid in gap.missing_point_ids]
            nearby_suspicious_count = sum(missing_near_suspicious[mid] for mid in gap.missing_point_ids)
            gap_id = f"gap_{global_gap_index:06d}"
            geo_values = _geo_for_local(postprocess_run, np.asarray([[gap.center_pixel.x, gap.center_pixel.y]]))
            longitude, latitude, geo_meta = geo_values[0][0, 0], geo_values[0][0, 1], geo_values[1]
            evidence_status = "review_near_suspicious_support" if nearby_suspicious_count else ("high_candidate" if gap.confidence >= 0.8 else "medium_candidate" if gap.confidence >= 0.5 else "low_candidate_review")
            gap_rows.append({"gap_id": gap_id, "plot_id": plot_id, "row_id": row_id, "start_slot": min(slots), "end_slot": max(slots), "estimated_missing_plants": len(slots), "local_x": gap.center_pixel.x, "local_y": gap.center_pixel.y, "latitude": latitude, "longitude": longitude, "coverage_count": min(coverages), "nearby_suspicious_count": nearby_suspicious_count, "gap_confidence": gap.confidence, "evidence_status": evidence_status, "geo_method": geo_meta["method"], "geo_quality": geo_meta["quality"], "horizontal_uncertainty_m": geo_meta["uncertainty"]})
            coords = [[point.x, point.y] for point in gap.polygon_pixel]
            coords.append(coords[0])
            gap_features.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [coords]}, "properties": gap_rows[-1]})
        expected_capacity = len(expected)
        estimated_missing = len(accepted_missing)
        valid_row_spacings = [float(row.median_spacing_px) for row in row_result.rows if row.median_spacing_px is not None]
        plot_rows.append({"plot_id": plot_id, "analysis_status": "complete_candidate_review_required", "confirmed_unique_plants": len(confirmed_selected), "low_support_unique_plants": len(low_support_selected), "unique_detected_plants": len(selected), "row_count": row_split["physical_rows"], "row_segment_count": row_split["row_segments"], "row_segment_cuts_at_polygon_boundary": row_split["segment_cuts"], "short_row_fragments_discarded": row_split["short_fragments_discarded"], "dominant_row_orientation_deg": row_result.dominant_orientation_deg, "along_row_spacing_local_px": float(np.median(valid_row_spacings)) if valid_row_spacings else "", "cross_row_spacing_local_px": row_result.inter_row_spacing_px, "expected_capacity": expected_capacity, "estimated_missing_plants": estimated_missing, "gap_count": len(gaps), "missing_rate": estimated_missing / expected_capacity if expected_capacity else "", "expected_rejected_outside_polygon": plot_rejected_outside, "candidate_missing_before_gates": len(candidate_missing), "rejected_boundary": plot_rejected_boundary, "rejected_coverage": plot_rejected_coverage, "downgraded_near_suspicious": plot_downgraded, "suppressed_by_low_support": plot_suppressed_low_support})

    unique.to_csv(postprocess_run / "unique_plants.csv", index=False)
    write_json(postprocess_run / "rows_local.geojson", {"type": "FeatureCollection", "coordinate_space": "registered_local_pixels", "features": row_features})
    write_json(postprocess_run / "gaps_local.geojson", {"type": "FeatureCollection", "coordinate_space": "registered_local_pixels", "features": gap_features})
    write_csv(postprocess_run / "expected_planting_points.csv", expected_rows, ["point_id", "plot_id", "row_id", "slot_index", "local_x", "local_y", "row_confidence", "spacing_confidence", "boundary_distance_local_px", "is_missing"])
    write_csv(postprocess_run / "missing_points.csv", missing_rows, ["missing_id", "expected_point_id", "plot_id", "row_id", "slot_index", "local_x", "local_y", "coverage_count", "nearby_suspicious_count", "confidence", "evidence_status", "reasons"])
    write_csv(postprocess_run / "gaps_summary.csv", gap_rows, ["gap_id", "plot_id", "row_id", "start_slot", "end_slot", "estimated_missing_plants", "local_x", "local_y", "latitude", "longitude", "coverage_count", "nearby_suspicious_count", "gap_confidence", "evidence_status", "geo_method", "geo_quality", "horizontal_uncertainty_m"])
    write_csv(postprocess_run / "plot_summary.csv", plot_rows, ["plot_id", "analysis_status", "confirmed_unique_plants", "low_support_unique_plants", "unique_detected_plants", "row_count", "row_segment_count", "row_segment_cuts_at_polygon_boundary", "short_row_fragments_discarded", "dominant_row_orientation_deg", "along_row_spacing_local_px", "cross_row_spacing_local_px", "expected_capacity", "estimated_missing_plants", "gap_count", "missing_rate", "expected_rejected_outside_polygon", "candidate_missing_before_gates", "rejected_boundary", "rejected_coverage", "downgraded_near_suspicious", "suppressed_by_low_support"])
    write_csv(postprocess_run / "rejected_missing_candidates.csv", rejected_rows, ["candidate_id", "plot_id", "row_id", "slot_index", "local_x", "local_y", "rejection_reason", "boundary_distance_local_px", "row_spacing_local_px", "boundary_distance_spacing_fraction", "coverage_count"])
    _render_gap_map(postprocess_run, plots, unique, row_features, expected_rows, missing_rows, gap_rows, rejected_rows, local_to_preview, int(gap_cfg["maximum_evidence_images"]))
    report = {"plot_count": len(plots["features"]), "rows": sum(int(row.get("row_count", 0)) for row in plot_rows), "row_segments": len(row_features), "row_segment_cuts_at_polygon_boundary": sum(int(row.get("row_segment_cuts_at_polygon_boundary", 0)) for row in plot_rows), "expected_planting_points": len(expected_rows), "accepted_missing_points": len(missing_rows), "gap_count": len(gap_rows), "estimated_missing_plants": sum(int(row["estimated_missing_plants"]) for row in gap_rows), "expected_points_rejected_outside_polygon": rejected_outside_polygon, "rejected_missing_boundary": rejected_boundary, "rejected_missing_low_coverage": rejected_coverage, "downgraded_missing_near_suspicious_support": downgraded_near_suspicious, "missing_candidates_suppressed_by_low_support_plants": suppressed_by_low_support, "status": "candidate_gaps_require_manual_review"}
    write_json(postprocess_run / "gap_analysis_report.json", report)
    return report


def _geo_for_local(postprocess_run: Path, points: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    report = read_json(postprocess_run / "georeference_report.json")
    if not report["valid"]:
        return np.full((len(points), 2), np.nan), {"method": report["method"], "quality": report["geo_quality"], "uncertainty": None}
    from .common import local_enu_to_latlon

    enu = transform_points(points, list_to_matrix(report["local_to_enu_similarity"]))
    origin = report["origin_wgs84"]
    lonlat = local_enu_to_latlon(enu[:, 0], enu[:, 1], origin["latitude"], origin["longitude"])
    return lonlat, {"method": report["method"], "quality": report["geo_quality"], "uncertainty": report["horizontal_uncertainty_m"]}


def _render_gap_map(postprocess_run: Path, plots: Mapping[str, Any], unique: pd.DataFrame, row_features: Sequence[Mapping[str, Any]], expected: Sequence[Mapping[str, Any]], missing: Sequence[Mapping[str, Any]], gaps: Sequence[Mapping[str, Any]], rejected: Sequence[Mapping[str, Any]], local_to_preview: np.ndarray, maximum_evidence_images: int) -> None:
    image = cv2.imread(str(postprocess_run / "survey_mosaic_preview.png"))
    coverage_image = cv2.imread(str(postprocess_run / "coverage" / "coverage_map.png"))
    if coverage_image is not None:
        image = cv2.addWeighted(image, 0.86, coverage_image, 0.14, 0)
    for feature in plots["features"]:
        for ring, is_hole in iter_polygon_rings(feature["geometry"]):
            local = np.asarray(ring, dtype=np.float64)
            preview = np.rint(transform_points(local, local_to_preview)).astype(
                np.int32
            )
            color = (180, 180, 180) if is_hole else (255, 255, 255)
            thickness = 1 if is_hole else 3
            cv2.polylines(image, [preview], True, color, thickness)
    sample = unique.iloc[:: max(1, len(unique) // 30000)][["local_x", "local_y"]].to_numpy(dtype=np.float64)
    for x, y in np.rint(transform_points(sample, local_to_preview)).astype(int):
        cv2.circle(image, (x, y), 1, (30, 220, 30), -1)
    for feature in row_features:
        points = np.rint(transform_points(np.asarray(feature["geometry"]["coordinates"]), local_to_preview)).astype(np.int32)
        cv2.polylines(image, [points], False, (255, 180, 0), 1)
    for row in expected:
        x, y = np.rint(transform_points(np.asarray([[row["local_x"], row["local_y"]]]), local_to_preview)[0]).astype(int)
        cv2.circle(image, (x, y), 1, (180, 180, 180), -1)
    for row in missing:
        x, y = np.rint(transform_points(np.asarray([[row["local_x"], row["local_y"]]]), local_to_preview)[0]).astype(int)
        color = (0, 180, 255) if row["evidence_status"] == "review_near_suspicious_support" else (0, 0, 255)
        cv2.circle(image, (x, y), 2, color, -1)
    for row in gaps:
        x, y = np.rint(transform_points(np.asarray([[row["local_x"], row["local_y"]]]), local_to_preview)[0]).astype(int)
        radius = max(3, min(9, 2 + int(math.log2(int(row["estimated_missing_plants"]) + 1))))
        cv2.circle(image, (x, y), radius, (255, 0, 255), 1)
    labelled = sorted(
        gaps,
        key=lambda row: (int(row["estimated_missing_plants"]), float(row["gap_confidence"])),
        reverse=True,
    )[: min(25, len(gaps))]
    for row in labelled:
        if int(row["estimated_missing_plants"]) < 2:
            continue
        x, y = np.rint(transform_points(np.asarray([[row["local_x"], row["local_y"]]]), local_to_preview)[0]).astype(int)
        cv2.putText(image, row["gap_id"], (x + 5, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)
    for row in rejected:
        x, y = np.rint(transform_points(np.asarray([[row["local_x"], row["local_y"]]]), local_to_preview)[0]).astype(int)
        cv2.drawMarker(image, (x, y), (0, 255, 255), cv2.MARKER_TILTED_CROSS, 4, 1)
    cv2.imwrite(str(postprocess_run / "gap_map_preview.png"), image)
    evidence = postprocess_run / "gap_evidence"
    if evidence.exists():
        shutil.rmtree(evidence)
    evidence.mkdir()
    selected_gaps = _select_gap_evidence(gaps, maximum_evidence_images)
    for row in selected_gaps:
        point = np.rint(transform_points(np.asarray([[row["local_x"], row["local_y"]]]), local_to_preview)[0]).astype(int)
        x1, y1 = max(0, point[0] - 180), max(0, point[1] - 180)
        x2, y2 = min(image.shape[1], point[0] + 180), min(image.shape[0], point[1] + 180)
        crop = image[y1:y2, x1:x2]
        if crop.size:
            cv2.imwrite(str(evidence / f"{row['gap_id']}_mosaic_context.png"), crop)
    _write_raw_frame_gap_evidence(postprocess_run, selected_gaps, evidence)
    rejected_evidence = evidence / "rejected"
    rejected_evidence.mkdir()
    selected_rejected = []
    for reason in ("polygon_boundary_exclusion_buffer", "insufficient_visibility_opportunity"):
        reason_rows = [row for row in rejected if row["rejection_reason"] == reason]
        seen_plots: set[str] = set()
        for row in sorted(reason_rows, key=lambda item: float(item["boundary_distance_spacing_fraction"])):
            plot_id = str(row["plot_id"])
            if plot_id in seen_plots:
                continue
            selected_rejected.append(row)
            seen_plots.add(plot_id)
            if len(seen_plots) >= 8:
                break
    rejected_for_renderer = [dict(row, gap_id=row["candidate_id"]) for row in selected_rejected]
    _write_raw_frame_gap_evidence(postprocess_run, rejected_for_renderer, rejected_evidence)


def _select_gap_evidence(gaps: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
    """Choose auditable evidence across size and confidence strata, not CSV order."""
    selected: list[Mapping[str, Any]] = []
    seen: set[str] = set()

    def add(rows: Sequence[Mapping[str, Any]]) -> None:
        for row in rows:
            gap_id = str(row["gap_id"])
            if gap_id not in seen and len(selected) < limit:
                selected.append(row)
                seen.add(gap_id)

    add(sorted(gaps, key=lambda row: int(row["estimated_missing_plants"]), reverse=True)[:10])
    for status in ("high_candidate", "medium_candidate", "low_candidate_review", "review_near_suspicious_support"):
        bucket = [row for row in gaps if row["evidence_status"] == status]
        add(sorted(bucket, key=lambda row: float(row["gap_confidence"]), reverse=True)[:5])
    add(sorted(gaps, key=lambda row: float(row["gap_confidence"])))
    return selected


def _write_raw_frame_gap_evidence(postprocess_run: Path, gaps: Sequence[Mapping[str, Any]], evidence_dir: Path) -> None:
    """Select the most central covering raw frame for each gap and render auditable context."""
    if not gaps:
        return
    mosaic_transform = read_json(postprocess_run / "mosaic_transform.json")
    audit = read_json(postprocess_run / "input_audit.json")
    width, height = mosaic_transform["full_frame_size"]
    roi_height = int(mosaic_transform["analysis_roi_height"])
    frame_transforms = {int(frame): list_to_matrix(matrix) for frame, matrix in mosaic_transform["frame_to_local"].items()}
    selected: dict[int, list[tuple[Mapping[str, Any], np.ndarray]]] = {}
    for row in gaps:
        local = np.asarray([[float(row["local_x"]), float(row["local_y"])]] )
        candidates = []
        for frame_number, frame_to_local in frame_transforms.items():
            frame_point = transform_points(local, np.linalg.inv(frame_to_local))[0]
            if 0 <= frame_point[0] < width and 0 <= frame_point[1] < roi_height:
                center_distance = float(np.linalg.norm(frame_point - np.asarray([width / 2, roi_height / 2])))
                candidates.append((center_distance, frame_number, frame_point))
        if candidates:
            _distance, frame_number, frame_point = min(candidates)
            selected.setdefault(frame_number, []).append((row, frame_point))
    capture = cv2.VideoCapture(str(audit["recorded_sources"]["video"]))
    for frame_number in sorted(selected):
        items = selected[frame_number]
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        ok, frame = capture.read()
        if not ok:
            LOGGER.warning("Could not decode raw evidence frame %d", frame_number)
            continue
        for row, frame_point in items:
            x, y = int(round(frame_point[0])), int(round(frame_point[1]))
            radius = 360
            x1, y1, x2, y2 = max(0, x - radius), max(0, y - radius), min(width, x + radius), min(height, y + radius)
            annotated = frame.copy()
            cv2.circle(annotated, (x, y), 28, (0, 0, 255), 5)
            cv2.putText(annotated, str(row["gap_id"]), (x + 35, y - 15), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
            crop = annotated[y1:y2, x1:x2]
            if crop.size:
                cv2.imwrite(str(evidence_dir / f"{row['gap_id']}_frame_{frame_number:06d}_raw_context.jpg"), crop)
    capture.release()
