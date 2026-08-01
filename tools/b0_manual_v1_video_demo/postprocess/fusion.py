"""Motion-compensated temporal fusion of plant detection observations."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import cv2
from scipy.spatial import cKDTree

from .common import quantiles, transform_points, weighted_median, write_json


LOGGER = logging.getLogger(__name__)


@dataclass
class _Track:
    observation_indices: list[int] = field(default_factory=list)
    frame_indices: set[int] = field(default_factory=set)
    last_frame_position: int = -1
    last_local: np.ndarray = field(default_factory=lambda: np.zeros(2, dtype=np.float64))


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self.parent = np.arange(size, dtype=np.int64)

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = int(self.parent[value])
        return value

    def union(self, first: int, second: int) -> int:
        a, b = self.find(first), self.find(second)
        if a == b:
            return a
        self.parent[b] = a
        return a


def greedy_one_to_one(
    previous: np.ndarray,
    current: np.ndarray,
    radius: float,
) -> list[tuple[int, int, float]]:
    """Sparse, deterministic nearest-pair assignment within a radius."""
    if len(previous) == 0 or len(current) == 0:
        return []
    tree = cKDTree(previous)
    distances, indices = tree.query(current, k=min(3, len(previous)), distance_upper_bound=radius)
    if distances.ndim == 1:
        distances, indices = distances[:, None], indices[:, None]
    candidates = []
    for current_index in range(len(current)):
        for distance, previous_index in zip(distances[current_index], indices[current_index]):
            if np.isfinite(distance) and previous_index < len(previous):
                candidates.append((float(distance), int(previous_index), int(current_index)))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    used_previous: set[int] = set()
    used_current: set[int] = set()
    matches = []
    for distance, previous_index, current_index in candidates:
        if previous_index in used_previous or current_index in used_current:
            continue
        used_previous.add(previous_index)
        used_current.add(current_index)
        matches.append((previous_index, current_index, distance))
    return matches


def _association_radius(audit: Mapping[str, Any], registration_report: Mapping[str, Any], cfg: Mapping[str, Any]) -> tuple[float, dict[str, float]]:
    spacing = float(audit["distributions"]["sample_within_frame_nearest_plant_distance_px"]["p50"])
    reprojection = float(registration_report["reprojection_error_p95_preview_px"]["p95"] or 0.0)
    preview_width = float(cfg["registration"]["preview_width"])
    full_width = float(cfg["video"]["width"])
    reprojection_full = reprojection * full_width / preview_width
    fusion_cfg = cfg["fusion"]
    raw = max(float(fusion_cfg["association_radius_min_px"]), spacing * float(fusion_cfg["association_spacing_fraction"]), reprojection_full * float(fusion_cfg["association_registration_multiplier"]))
    maximum = spacing * float(fusion_cfg["association_radius_max_spacing_fraction"])
    chosen = min(raw, maximum)
    return chosen, {"median_plant_spacing_local_px": spacing, "registration_p95_full_px": reprojection_full, "raw_radius_local_px": raw, "maximum_radius_local_px": maximum}


def fuse_detections(
    detections_path: Path,
    frame_indices: Sequence[int],
    transforms: Mapping[int, np.ndarray],
    segments: Mapping[int, int],
    footprints: Mapping[int, np.ndarray],
    output_dir: Path,
    audit: Mapping[str, Any],
    registration_report: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Associate observations in registered local coordinates and emit auditable tracks."""
    columns = ["frame_index", "video_time_seconds", "detection_id", "confidence", "center_x", "center_y", "x1", "y1", "x2", "y2"]
    detections = pd.read_csv(detections_path, usecols=columns)
    allowed = set(int(value) for value in frame_indices)
    detections = detections[detections["frame_index"].isin(allowed)].reset_index(drop=True)
    local_xy = np.empty((len(detections), 2), dtype=np.float64)
    frame_positions = {number: position for position, number in enumerate(frame_indices)}
    frame_groups = detections.groupby("frame_index", sort=True).indices
    for frame_number, indices in frame_groups.items():
        source = detections.loc[indices, ["center_x", "center_y"]].to_numpy(dtype=np.float64)
        local_xy[indices] = transform_points(source, transforms[int(frame_number)])
    detections["local_x"] = local_xy[:, 0]
    detections["local_y"] = local_xy[:, 1]

    radius, radius_basis = _association_radius(audit, registration_report, config)
    max_gap = int(config["fusion"]["max_track_gap_processed_frames"])
    tracks: list[_Track] = []
    assignment = np.full(len(detections), -1, dtype=np.int64)
    active_ids: list[int] = []
    pair_distance_samples: list[float] = []
    for frame_number in frame_indices:
        if frame_number not in frame_groups:
            continue
        current_indices = np.asarray(frame_groups[frame_number], dtype=np.int64)
        current = local_xy[current_indices]
        position = frame_positions[frame_number]
        active_ids = [track_id for track_id in active_ids if tracks[track_id].last_frame_position >= position - max_gap and segments[frame_number] == segments[int(detections.loc[tracks[track_id].observation_indices[-1], "frame_index"])]]
        previous = np.asarray([tracks[track_id].last_local for track_id in active_ids], dtype=np.float64)
        matches = greedy_one_to_one(previous, current, radius)
        matched_current = set()
        for active_index, current_index, distance in matches:
            track_id = active_ids[active_index]
            observation_index = int(current_indices[current_index])
            tracks[track_id].observation_indices.append(observation_index)
            tracks[track_id].frame_indices.add(int(frame_number))
            tracks[track_id].last_frame_position = position
            tracks[track_id].last_local = current[current_index]
            assignment[observation_index] = track_id
            matched_current.add(current_index)
            pair_distance_samples.append(distance)
        for current_index, observation_index_value in enumerate(current_indices):
            if current_index in matched_current:
                continue
            observation_index = int(observation_index_value)
            track_id = len(tracks)
            tracks.append(_Track([observation_index], {int(frame_number)}, position, current[current_index].copy()))
            assignment[observation_index] = track_id
            active_ids.append(track_id)

    # Merge geometrically coincident tracklets, while prohibiting same-frame duplicates.
    provisional_centers = np.asarray([np.median(local_xy[track.observation_indices], axis=0) for track in tracks])
    merge_radius = radius * float(config["fusion"]["tracklet_merge_radius_fraction"])
    pairs = sorted(cKDTree(provisional_centers).query_pairs(merge_radius), key=lambda pair: float(np.linalg.norm(provisional_centers[pair[0]] - provisional_centers[pair[1]])))
    dsu = _DisjointSet(len(tracks))
    component_frames = {index: set(track.frame_indices) for index, track in enumerate(tracks)}
    component_segments = {
        index: {int(segments[int(detections.loc[observation, "frame_index"])]) for observation in track.observation_indices}
        for index, track in enumerate(tracks)
    }
    rejected_same_frame = 0
    rejected_cross_segment = 0
    for first, second in pairs:
        root_first, root_second = dsu.find(first), dsu.find(second)
        if root_first == root_second:
            continue
        if component_frames[root_first] & component_frames[root_second]:
            rejected_same_frame += 1
            continue
        if component_segments[root_first] != component_segments[root_second]:
            rejected_cross_segment += 1
            continue
        merged_root = dsu.union(root_first, root_second)
        other_root = root_second if merged_root == root_first else root_first
        component_frames[merged_root] |= component_frames[other_root]
        component_segments[merged_root] |= component_segments[other_root]

    root_to_observations: dict[int, list[int]] = {}
    for track_id, track in enumerate(tracks):
        root = dsu.find(track_id)
        root_to_observations.setdefault(root, []).extend(track.observation_indices)
    ordered_components = sorted(root_to_observations.values(), key=lambda items: min(items))
    final_assignment = np.full(len(detections), -1, dtype=np.int64)
    for plant_index, observation_indices in enumerate(ordered_components):
        final_assignment[observation_indices] = plant_index
    if np.any(final_assignment < 0):
        raise AssertionError("Every observation must belong to exactly one unique-plant candidate")

    unique_rows = []
    centers = np.empty((len(ordered_components), 2), dtype=np.float64)
    observation_counts = np.empty(len(ordered_components), dtype=np.int32)
    for plant_index, observation_indices in enumerate(ordered_components):
        rows = detections.iloc[observation_indices]
        frames = rows["frame_index"].astype(int).to_numpy()
        if len(np.unique(frames)) != len(frames):
            raise AssertionError(f"Plant {plant_index} contains two observations from one frame")
        confidence = rows["confidence"].to_numpy(dtype=np.float64)
        points = local_xy[observation_indices]
        x = weighted_median(points[:, 0], np.maximum(confidence, 1e-6))
        y = weighted_median(points[:, 1], np.maximum(confidence, 1e-6))
        centers[plant_index] = [x, y]
        observation_counts[plant_index] = len(rows)
        unique_rows.append({
            "plant_id": f"plant_{plant_index + 1:07d}",
            "local_x": x,
            "local_y": y,
            "observation_count": len(rows),
            "first_frame": int(frames.min()),
            "last_frame": int(frames.max()),
            "mean_confidence": float(confidence.mean()),
            "max_confidence": float(confidence.max()),
            "temporal_support": int(len(np.unique(frames))),
        })

    repeated_scatter = np.linalg.norm(local_xy - centers[final_assignment], axis=1)

    opportunity = np.zeros(len(centers), dtype=np.int32)
    width, roi_height = int(config["video"]["width"]), int(config["coverage"]["roi_height"])
    for frame_number in frame_indices:
        frame_points = transform_points(centers, np.linalg.inv(transforms[frame_number]))
        inside = (frame_points[:, 0] >= 0) & (frame_points[:, 0] < width) & (frame_points[:, 1] >= 0) & (frame_points[:, 1] < roi_height)
        opportunity += inside.astype(np.int32)
    confirmed_min = int(config["fusion"]["confirmed_min_observations"])
    suspicious_threshold = int(config["fusion"]["suspicious_singleton_min_opportunity"])
    for index, row in enumerate(unique_rows):
        count, seen = int(observation_counts[index]), int(opportunity[index])
        if count >= confirmed_min:
            status = "confirmed"
        elif count == 1 and seen <= 1:
            status = "boundary_singleton"
        elif count == 1 and seen >= suspicious_threshold:
            status = "suspicious_singleton"
        else:
            status = "low_support"
        row.update({
            "visibility_opportunity": seen,
            "support_status": status,
            "plot_id": "",
            "latitude": "",
            "longitude": "",
            "geo_method": "local_only_pending_geo_QA",
            "geo_quality": "not_evaluated",
            "horizontal_uncertainty_m": "",
        })
    unique_df = pd.DataFrame(unique_rows)
    unique_df.to_csv(output_dir / "unique_plants.csv", index=False)

    plant_ids = np.asarray([f"plant_{value + 1:07d}" for value in final_assignment], dtype=object)
    observation_df = detections.copy()
    observation_df.insert(0, "plant_id", plant_ids)
    observation_df.insert(1, "observation_id", [f"frame_{int(frame):06d}_det_{int(det):05d}" for frame, det in zip(observation_df["frame_index"], observation_df["detection_id"])])
    observation_df["registration_segment_id"] = observation_df["frame_index"].map(segments).astype(int)
    observation_df.to_csv(output_dir / "track_observations.csv", index=False)

    status_counts = unique_df["support_status"].value_counts().to_dict()
    sensitivity = []
    for factor in (0.8, 1.0, 1.2):
        tested = min(radius * factor, radius_basis["maximum_radius_local_px"])
        retained = int(np.sum(np.asarray(pair_distance_samples) <= tested))
        sensitivity.append({
            "factor": factor,
            "radius_local_px": tested,
            "fraction_of_median_spacing": tested / radius_basis["median_plant_spacing_local_px"],
            "accepted_temporal_links_under_fixed_assignment": retained,
            "retained_fraction_of_selected_links": retained / max(1, len(pair_distance_samples)),
        })
    report = {
        "total_detection_observations": int(len(detections)),
        "provisional_tracklets": len(tracks),
        "unique_plant_candidates": int(len(unique_df)),
        "confirmed_unique_plants": int(status_counts.get("confirmed", 0)),
        "low_support_unique_plants": int(status_counts.get("low_support", 0) + status_counts.get("boundary_singleton", 0) + status_counts.get("suspicious_singleton", 0)),
        "support_status_counts": status_counts,
        "observation_per_plant": quantiles(observation_counts),
        "visibility_opportunity": quantiles(opportunity),
        "association_distance_local_px": quantiles(pair_distance_samples),
        "repeated_location_scatter_observation_local_px": quantiles(repeated_scatter),
        "association_radius_local_px": radius,
        "association_radius_basis": radius_basis,
        "map_merge_radius_local_px": merge_radius,
        "map_merge_candidates_rejected_same_frame": rejected_same_frame,
        "map_merge_candidates_rejected_cross_registration_segment": rejected_cross_segment,
        "sensitivity_parameters": sensitivity,
        "sensitivity_interpretation": "Fixed-assignment link retention around the selected radius; upper radius remains capped below 0.42 of normal spacing to protect adjacent plants.",
        "algorithm": "registered-map sparse cKDTree candidates + deterministic greedy one-to-one + constrained map-level tracklet union",
        "invariants": {
            "one_owner_per_observation": bool(np.all(final_assignment >= 0)),
            "no_two_observations_same_frame_per_plant": True,
            "association_radius_below_half_normal_spacing": radius < 0.5 * radius_basis["median_plant_spacing_local_px"],
        },
    }
    write_json(output_dir / "fusion_report.json", report)
    write_json(output_dir / "sensitivity_report.json", {"selected_radius_local_px": radius, "normal_spacing_local_px": radius_basis["median_plant_spacing_local_px"], "scenarios": sensitivity, "interpretation": report["sensitivity_interpretation"]})
    write_track_history_qa(output_dir, observation_df)
    return unique_df, observation_df, report


def write_unique_plant_overlay(output_dir: Path, mosaic: np.ndarray, local_to_preview: np.ndarray, unique_df: pd.DataFrame) -> Path:
    image = mosaic.copy()
    points = unique_df[["local_x", "local_y"]].to_numpy(dtype=np.float64)
    preview = transform_points(points, local_to_preview)
    statuses = unique_df["support_status"].tolist()
    color = {"confirmed": (30, 220, 30), "low_support": (0, 180, 255), "boundary_singleton": (255, 160, 0), "suspicious_singleton": (0, 0, 255)}
    for point, status in zip(preview, statuses):
        x, y = int(round(point[0])), int(round(point[1]))
        if 0 <= x < image.shape[1] and 0 <= y < image.shape[0]:
            cv_color = color.get(status, (255, 255, 255))
            cv2.circle(image, (x, y), 1, cv_color, -1)
    path = output_dir / "unique_plants_mosaic_overlay.png"
    cv2.imwrite(str(path), image)
    return path


def write_track_history_qa(output_dir: Path, observations: pd.DataFrame, example_count: int = 9) -> Path:
    """Render observation-history scatter for representative long tracks."""
    import matplotlib.pyplot as plt

    counts = observations.groupby("plant_id").size().sort_values(ascending=False)
    if counts.empty:
        raise ValueError("Cannot render track history without observations")
    quantile_positions = np.linspace(0, len(counts) - 1, min(example_count, len(counts)), dtype=int)
    selected = counts.index[quantile_positions]
    columns = 3
    rows = int(math.ceil(len(selected) / columns))
    figure, axes = plt.subplots(rows, columns, figsize=(11, 3.4 * rows), squeeze=False)
    for axis, plant_id in zip(axes.flat, selected):
        track = observations[observations["plant_id"] == plant_id].sort_values("frame_index")
        x = track["local_x"].to_numpy(dtype=np.float64)
        y = track["local_y"].to_numpy(dtype=np.float64)
        axis.plot(x - np.median(x), y - np.median(y), "o-", markersize=2, linewidth=0.7)
        axis.set_title(f"{plant_id} • n={len(track)} • f{track.frame_index.min()}–{track.frame_index.max()}")
        axis.set_aspect("equal", adjustable="datalim")
        axis.grid(alpha=0.25)
        axis.set_xlabel("local x residual (px)")
        axis.set_ylabel("local y residual (px)")
    for axis in axes.flat[len(selected) :]:
        axis.axis("off")
    figure.suptitle("Motion-compensated unique-plant observation histories")
    figure.tight_layout()
    path = output_dir / "track_history_examples.png"
    figure.savefig(path, dpi=160)
    plt.close(figure)
    return path
