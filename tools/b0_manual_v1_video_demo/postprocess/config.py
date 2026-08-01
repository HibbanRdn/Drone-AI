"""Post-processing configuration and validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


DEFAULT_CONFIG: dict[str, Any] = {
    "registration": {
        "preview_width": 960,
        "feature": "sift",
        "max_features": 3000,
        "ratio_test": 0.75,
        "ransac_reprojection_px_preview": 2.5,
        "min_mutual_matches": 80,
        "min_inliers": 50,
        "min_inlier_ratio": 0.40,
        "max_p95_reprojection_px_preview": 3.0,
        "min_scale": 0.94,
        "max_scale": 1.06,
        "max_rotation_deg_per_edge": 2.0,
        "homography_min_inlier_gain": 0.08,
        "max_homography_perspective": 0.00015,
        "fallbacks": ["klt"],
    },
    "mosaic": {
        "max_preview_width": 4200,
        "max_preview_height": 2400,
        "minimum_keyframe_center_displacement_px": 900,
        "jpeg_quality": 92,
    },
    "fusion": {
        "association_spacing_fraction": 0.34,
        "association_registration_multiplier": 2.5,
        "association_radius_min_px": 4.0,
        "association_radius_max_spacing_fraction": 0.42,
        "max_track_gap_processed_frames": 3,
        "tracklet_merge_radius_fraction": 0.75,
        "confirmed_min_observations": 3,
        "suspicious_singleton_min_opportunity": 5,
    },
    "coverage": {"roi_height": 2048, "minimum_gap_opportunity": 3},
    "camera_profile": {
        "profile_id": "dji_enterprise_wide_equiv24_fov84_assumed_diagonal_16x9",
        "selection_evidence": "SRT focal_length=24.0, digital_zoom=1.0, aperture=2.8; official DJI wide profile",
        "diagonal_fov_deg": 84.0,
        "fov_semantics": "assumed_diagonal_not_proven_for_video_crop",
        "equivalent_focal_length_mm": 24.0,
        "physical_focal_length_mm": None,
        "sensor_model": "4/3 CMOS profile assumption; aircraft model not proven by MP4",
    },
    "georeferencing": {
        "enabled": True,
        "minimum_path_cross_track_ratio": 0.05,
        "maximum_p95_anchor_residual_m": 2.0,
        "maximum_scale_disagreement_fraction": 0.35,
        "minimum_direction_cosine_p50": 0.50,
        "minimum_positive_direction_fraction": 0.80,
        "rtk_status": "unknown",
        "ground_model": "flat_ground_relative_altitude_takeoff_reference",
    },
    "plots": {"coordinate_space": "mosaic_local_full_resolution_pixels"},
    "gap_analysis": {
        "minimum_plants_per_plot": 40,
        "minimum_plants_per_row": 5,
        "row_angle_bin_deg": 2.0,
        "row_assignment_band_spacing_fraction": 0.42,
        "missing_match_spacing_fraction": 0.42,
        "boundary_buffer_row_spacing_fraction": 2.5,
        "minimum_coverage_opportunity": 3,
        "maximum_evidence_images": 30,
    },
}


def merged_config(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result = deepcopy(DEFAULT_CONFIG)
    if overrides:
        _deep_merge(result, overrides)
    validate_config(result)
    return result


def _deep_merge(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def validate_config(config: Mapping[str, Any]) -> None:
    registration = config["registration"]
    if int(registration["preview_width"]) < 320:
        raise ValueError("registration.preview_width must be at least 320")
    if not 0 < float(registration["ratio_test"]) < 1:
        raise ValueError("registration.ratio_test must be in (0, 1)")
    fusion = config["fusion"]
    if not 0 < float(fusion["association_spacing_fraction"]) < 0.5:
        raise ValueError("fusion association radius must be less than half normal spacing")
    if int(config["coverage"]["minimum_gap_opportunity"]) < 1:
        raise ValueError("minimum coverage opportunity must be positive")
