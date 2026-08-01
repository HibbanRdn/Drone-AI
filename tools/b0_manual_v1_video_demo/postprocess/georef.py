"""Conservative approximate video/SRT georeferencing with an explicit QA gate."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .common import latlon_to_local_enu, local_enu_to_latlon, matrix_to_list, quantiles, transform_points, write_json


def fit_similarity(source: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares 2-D similarity transform mapping source to target."""
    source = np.asarray(source, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 2 or len(source) < 2:
        raise ValueError("Similarity fitting requires matching Nx2 arrays")
    source_mean, target_mean = source.mean(axis=0), target.mean(axis=0)
    centered_source, centered_target = source - source_mean, target - target_mean
    covariance = centered_target.T @ centered_source / len(source)
    u, singular, vt = np.linalg.svd(covariance)
    correction = np.eye(2)
    if np.linalg.det(u @ vt) < 0:
        correction[-1, -1] = -1
    rotation = u @ correction @ vt
    variance = float(np.sum(centered_source * centered_source) / len(source))
    scale = float(np.sum(singular * np.diag(correction)) / variance)
    translation = target_mean - scale * (rotation @ source_mean)
    matrix = np.eye(3, dtype=np.float64)
    matrix[:2, :2] = scale * rotation
    matrix[:2, 2] = translation
    residuals = np.linalg.norm(transform_points(source, matrix) - target, axis=1)
    return matrix, residuals


def robust_similarity(source: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    keep = np.ones(len(source), dtype=bool)
    matrix = np.eye(3)
    residuals = np.full(len(source), np.inf)
    for _ in range(5):
        matrix, _ = fit_similarity(source[keep], target[keep])
        residuals = np.linalg.norm(transform_points(source, matrix) - target, axis=1)
        median = float(np.median(residuals[keep]))
        mad = float(np.median(np.abs(residuals[keep] - median)))
        threshold = max(0.5, median + 3.5 * 1.4826 * mad)
        new_keep = residuals <= threshold
        if new_keep.sum() < 3 or np.array_equal(new_keep, keep):
            break
        keep = new_keep
    return matrix, residuals, keep


def evaluate_and_apply_georeference(
    telemetry_path: Path,
    frame_indices: Sequence[int],
    transforms: Mapping[int, np.ndarray],
    unique_plants_path: Path,
    output_dir: Path,
    audit: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    telemetry = pd.read_csv(telemetry_path).set_index("frame_index")
    telemetry = telemetry.loc[list(frame_indices)]
    width, height = float(config["video"]["width"]), float(config["video"]["height"])
    local_centers = np.asarray([transform_points(np.asarray([[width / 2, height / 2]]), transforms[number])[0] for number in frame_indices])
    origin_latitude = float(telemetry["latitude"].iloc[0])
    origin_longitude = float(telemetry["longitude"].iloc[0])
    gps_enu = latlon_to_local_enu(telemetry["latitude"].to_numpy(), telemetry["longitude"].to_numpy(), origin_latitude, origin_longitude)
    matrix, residuals, inliers = robust_similarity(local_centers, gps_enu)
    predicted_enu = transform_points(local_centers, matrix)
    visual_steps = np.diff(predicted_enu, axis=0)
    gps_steps = np.diff(gps_enu, axis=0)
    visual_norm = np.linalg.norm(visual_steps, axis=1)
    gps_norm = np.linalg.norm(gps_steps, axis=1)
    direction_valid = (visual_norm > 0.01) & (gps_norm > 0.01)
    direction_cosine = np.sum(visual_steps[direction_valid] * gps_steps[direction_valid], axis=1) / (
        visual_norm[direction_valid] * gps_norm[direction_valid]
    )
    direction_p50 = float(np.median(direction_cosine)) if len(direction_cosine) else -1.0
    positive_direction_fraction = float(np.mean(direction_cosine > 0)) if len(direction_cosine) else 0.0
    scale = float(math.sqrt(abs(np.linalg.det(matrix[:2, :2]))))
    expected_gsd = float(audit["preliminary_coverage"]["assumed_ground_footprint_width_m"]) / width
    scale_disagreement = abs(scale - expected_gsd) / expected_gsd
    cross_track_ratio = float(audit["flight_path"]["pca_cross_track_to_along_track_ratio"])
    p95_residual = float(np.quantile(residuals, 0.95))
    geo_cfg = config["georeferencing"]
    reasons = []
    if cross_track_ratio < float(geo_cfg["minimum_path_cross_track_ratio"]):
        reasons.append("flight_path_cross_track_geometry_underconstrained")
    if p95_residual > float(geo_cfg["maximum_p95_anchor_residual_m"]):
        reasons.append("high_pose_alignment_residual")
    if scale_disagreement > float(geo_cfg["maximum_scale_disagreement_fraction"]):
        reasons.append("visual_scale_disagrees_with_assumed_camera_profile")
    if direction_p50 < float(geo_cfg["minimum_direction_cosine_p50"]) or positive_direction_fraction < float(geo_cfg["minimum_positive_direction_fraction"]):
        reasons.append("visual_motion_direction_inconsistent_with_gps")
    if audit["telemetry_semantics"]["gimbal_near_nadir"] is not True:
        reasons.append("camera_not_verified_near_nadir")
    valid = not reasons
    uncertainty = max(p95_residual, expected_gsd * float(audit["distributions"]["sample_within_frame_nearest_plant_distance_px"]["p50"])) if valid else None
    method = "approximate_video_srt_robust_similarity_flat_ground" if valid else "local_only_geo_QA_failed"
    quality = "approximate" if valid else "invalid"

    unique = pd.read_csv(unique_plants_path)
    if valid:
        enu = transform_points(unique[["local_x", "local_y"]].to_numpy(dtype=np.float64), matrix)
        lonlat = local_enu_to_latlon(enu[:, 0], enu[:, 1], origin_latitude, origin_longitude)
        unique["longitude"] = lonlat[:, 0]
        unique["latitude"] = lonlat[:, 1]
        unique["horizontal_uncertainty_m"] = uncertainty
    else:
        unique["longitude"] = np.nan
        unique["latitude"] = np.nan
        unique["horizontal_uncertainty_m"] = np.nan
    unique["geo_method"] = method
    unique["geo_status"] = "valid_approximate" if valid else "invalid_local_only"
    unique["geo_quality"] = quality
    unique["assumed_camera_profile"] = config["camera_profile"]["profile_id"]
    unique["rtk_status"] = audit["telemetry_semantics"]["rtk_status"]
    unique["ground_model"] = config["georeferencing"]["ground_model"]
    unique.to_csv(unique_plants_path, index=False)

    report = {
        "valid": valid,
        "status": "valid_approximate" if valid else "invalid_local_only",
        "failure_reasons": reasons,
        "method": method,
        "geo_quality": quality,
        "local_to_enu_similarity": matrix_to_list(matrix),
        "origin_wgs84": {"latitude": origin_latitude, "longitude": origin_longitude},
        "alignment_residual_m": quantiles(residuals),
        "alignment_inlier_count": int(inliers.sum()),
        "frame_count": len(frame_indices),
        "visual_scale_m_per_local_px": scale,
        "camera_profile_expected_gsd_m_per_px": expected_gsd,
        "scale_disagreement_fraction": scale_disagreement,
        "flight_cross_track_to_along_track_ratio": cross_track_ratio,
        "visual_to_gps_step_direction_cosine": quantiles(direction_cosine),
        "visual_to_gps_positive_direction_fraction": positive_direction_fraction,
        "horizontal_uncertainty_m": uncertainty,
        "assumed_camera_profile": config["camera_profile"]["profile_id"],
        "camera_coordinate_convention": "raw image x right, y down; near-nadir frame center used only for SRT anchor fitting",
        "euler_angle_order": "not applied: gimbal pitch near nadir; yaw semantics unknown",
        "rtk_status": audit["telemetry_semantics"]["rtk_status"],
        "ground_model": config["georeferencing"]["ground_model"],
        "warning": "Any WGS84 location from video/SRT is approximate and not survey-grade. Invalid QA leaves all latitude/longitude null.",
    }
    write_json(output_dir / "georeference_report.json", report)
    return report
