"""Input audit for a completed B0 Manual-v1 inference run."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from .common import (
    diagonal_fov_to_axes,
    ffprobe_video,
    latlon_to_local_enu,
    quantiles,
    read_json,
    sha256_file,
    utc_now_iso,
)


REQUIRED_RUN_FILES = (
    "run_config.json",
    "run_summary.json",
    "environment.json",
    "srt_parse_report.json",
    "telemetry_sync.csv",
    "detections.csv",
    "frame_summary.csv",
)


def audit_inference_run(inference_run: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    inference_run = inference_run.resolve()
    missing = [name for name in REQUIRED_RUN_FILES if not (inference_run / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Inference run missing required files: {missing}")
    run_config = read_json(inference_run / "run_config.json")
    run_summary = read_json(inference_run / "run_summary.json")
    environment = read_json(inference_run / "environment.json")
    srt_report = read_json(inference_run / "srt_parse_report.json")
    video_path = Path(run_config["video_path"]).resolve()
    srt_path = Path(run_config["srt_path"]).resolve()
    model_path = Path(run_config["model_path"]).resolve()
    for source in (video_path, srt_path, model_path):
        if not source.is_file():
            raise FileNotFoundError(f"Recorded source no longer exists: {source}")

    current_hashes = {
        "model": sha256_file(model_path),
        "video": sha256_file(video_path),
        "srt": sha256_file(srt_path),
    }
    recorded_hashes = run_summary["source_hashes_before"]
    hash_match = {key: current_hashes[key] == recorded_hashes.get(key) for key in current_hashes}
    if not all(hash_match.values()):
        raise RuntimeError(f"Source hash mismatch against inference run: {hash_match}")

    frame_summary = pd.read_csv(inference_run / "frame_summary.csv")
    telemetry = pd.read_csv(inference_run / "telemetry_sync.csv")
    confidence_parts: list[np.ndarray] = []
    observation_count = 0
    sampled_frames = set(frame_summary["frame_index"].iloc[::25].astype(int).tolist())
    sample_coordinates: dict[int, list[np.ndarray]] = {number: [] for number in sampled_frames}
    detection_columns: list[str] | None = None
    for chunk in pd.read_csv(
        inference_run / "detections.csv",
        usecols=lambda name: name in {"frame_index", "confidence", "center_x", "center_y"},
        chunksize=100_000,
    ):
        if detection_columns is None:
            detection_columns = list(pd.read_csv(inference_run / "detections.csv", nrows=0).columns)
        observation_count += len(chunk)
        confidence_parts.append(chunk["confidence"].to_numpy(dtype=np.float64))
        sample = chunk[chunk["frame_index"].isin(sampled_frames)]
        for frame_number, rows in sample.groupby("frame_index"):
            sample_coordinates[int(frame_number)].append(
                rows[["center_x", "center_y"]].to_numpy(dtype=np.float64)
            )
    confidences = np.concatenate(confidence_parts)
    nearest_neighbor_distances = []
    for parts in sample_coordinates.values():
        if not parts:
            continue
        points = np.vstack(parts)
        distances, _indices = cKDTree(points).query(points, k=2)
        nearest_neighbor_distances.extend(distances[:, 1].tolist())

    latitude = telemetry["latitude"].to_numpy(dtype=np.float64)
    longitude = telemetry["longitude"].to_numpy(dtype=np.float64)
    origin_latitude, origin_longitude = float(latitude[0]), float(longitude[0])
    enu = latlon_to_local_enu(latitude, longitude, origin_latitude, origin_longitude)
    centered = enu - enu.mean(axis=0)
    _u, singular_values, vh = np.linalg.svd(centered, full_matrices=False)
    path_axis = vh[0]
    progress = centered @ path_axis
    if progress[-1] < progress[0]:
        progress = -progress
    steps = np.linalg.norm(np.diff(enu, axis=0), axis=1)
    reversals = int(np.sum(np.diff(progress) < -0.05))
    nonadjacent_distances = [
        float(np.linalg.norm(enu[first] - enu[second]))
        for first in range(len(enu))
        for second in range(first + 30, len(enu))
    ]
    camera_profile = config["camera_profile"]
    video_probe = ffprobe_video(video_path)
    stream = video_probe["streams"][0]
    horizontal_fov, vertical_fov = diagonal_fov_to_axes(
        float(camera_profile["diagonal_fov_deg"]), int(stream["width"]), int(stream["height"])
    )
    median_altitude = float(np.median(telemetry["relative_altitude"].to_numpy()))
    footprint_width_m = 2 * median_altitude * math.tan(math.radians(horizontal_fov) / 2)
    footprint_height_m = 2 * median_altitude * math.tan(math.radians(vertical_fov) / 2)
    median_step = float(np.median(steps))
    preliminary_opportunity = footprint_height_m / median_step if median_step > 0 else None

    focal_values = [entry.get("focal_length") for entry in srt_report["first_three_entries"]]
    zoom_values = [entry.get("digital_zoom") for entry in srt_report["first_three_entries"]]
    aperture_values = [entry.get("aperture") for entry in srt_report["first_three_entries"]]
    encoder_tag = video_probe.get("format", {}).get("tags", {}).get("encoder", "unknown")
    camera_evidence = {
        "classification": "wide_profile_supported_but_aircraft_model_not_proven",
        "srt_focal_length_values": focal_values,
        "srt_digital_zoom_values": zoom_values,
        "srt_aperture_values": aperture_values,
        "mp4_encoder_tag": encoder_tag,
        "configured_profile": camera_profile,
        "derived_horizontal_fov_deg": horizontal_fov,
        "derived_vertical_fov_deg": vertical_fov,
        "warning": (
            "24 mm is treated as 35-mm-equivalent metadata. It is never combined with a "
            "4/3 sensor size as a physical focal length. FOV 84 semantics for the 16:9 video "
            "crop are not proven and remain a configuration assumption."
        ),
    }
    fields_found = set(srt_report.get("fields_found", []))
    audit = {
        "generated_at": utc_now_iso(),
        "inference_run": str(inference_run),
        "required_files_present": True,
        "run_status": run_summary.get("status"),
        "recorded_sources": {
            "video": str(video_path),
            "srt": str(srt_path),
            "model": str(model_path),
        },
        "source_hashes_recorded": recorded_hashes,
        "source_hashes_current": current_hashes,
        "source_hash_match": hash_match,
        "video_probe": video_probe,
        "run_config": run_config,
        "run_summary": run_summary,
        "environment": environment,
        "schemas": {
            "detections_csv_columns": detection_columns,
            "detections_jsonl_first_object_keys": (
                _jsonl_first_keys(inference_run / "detections.jsonl")
                if (inference_run / "detections.jsonl").is_file()
                else None
            ),
            "detections_jsonl_status": (
                "present"
                if (inference_run / "detections.jsonl").is_file()
                else "not_emitted_csv_is_canonical"
            ),
        },
        "distributions": {
            "confidence": quantiles(confidences),
            "plant_count_per_processed_frame": quantiles(frame_summary["plant_count"].to_numpy()),
            "gimbal_pitch_deg": quantiles(telemetry["gimbal_pitch"].to_numpy()),
            "gimbal_yaw_deg": quantiles(telemetry["gimbal_yaw"].to_numpy()),
            "gimbal_roll_deg": quantiles(telemetry["gimbal_roll"].to_numpy()),
            "relative_altitude_m": quantiles(telemetry["relative_altitude"].to_numpy()),
            "absolute_altitude_m": quantiles(telemetry["absolute_altitude"].to_numpy()),
            "latitude": quantiles(latitude),
            "longitude": quantiles(longitude),
            "gps_step_between_processed_frames_m": quantiles(steps),
            "sample_within_frame_nearest_plant_distance_px": quantiles(nearest_neighbor_distances),
            "srt_sync_delta_seconds": quantiles(telemetry["sync_delta_seconds"].to_numpy()),
        },
        "flight_path": {
            "path_length_m": float(steps.sum()),
            "endpoint_distance_m": float(np.linalg.norm(enu[-1] - enu[0])),
            "pca_cross_track_to_along_track_ratio": float(singular_values[1] / singular_values[0]),
            "reverse_steps_over_0_05m": reversals,
            "nearest_nonadjacent_30_processed_frame_distance_m": (
                min(nonadjacent_distances) if nonadjacent_distances else None
            ),
            "interpretation": "predominantly_one_way_nearly_straight_no_material_revisit",
        },
        "camera": camera_evidence,
        "telemetry_semantics": {
            "gimbal_near_nadir": bool(np.max(np.abs(telemetry["gimbal_pitch"] + 90.0)) <= 0.2),
            "gimbal_yaw_semantics": "unknown_absolute_or_relative_not_proven_by_srt",
            "rtk_status": "unknown" if "rtk_status" not in fields_found else "recorded",
            "laser_rangefinder_distance": "not_recorded",
            "relative_altitude_interpretation": "takeoff_reference_not_verified_AGL_at_each_plant",
        },
        "preliminary_coverage": {
            "assumed_ground_footprint_width_m": footprint_width_m,
            "assumed_ground_footprint_height_m": footprint_height_m,
            "median_processed_frame_gps_step_m": median_step,
            "approximate_frame_opportunity_along_track": preliminary_opportunity,
            "status": "preliminary_camera_profile_assumption; final opportunity computed from registered footprints",
        },
        "counts": {
            "total_detection_observations": observation_count,
            "processed_frames": int(len(frame_summary)),
            "source_video_frames": int(stream["nb_frames"]),
            "srt_blocks": int(srt_report["block_count"]),
        },
        "official_references": [
            {
                "url": "https://enterprise.dji.com/matrice-4-series/specs",
                "use": "camera, video, gimbal, RTK capability specifications; capability is not flight status",
            },
            {
                "url": "https://enterprise.dji.com/geospatial/land-survey",
                "use": "DJI mapping workflow and Matrice 4E mapping context",
            },
            {
                "url": "https://enterprise-insights.dji.com/blog/orthomosaics",
                "use": "orthorectification and ground-control limitations of ordinary aerial frames",
            },
        ],
    }
    return audit


def audit_markdown(audit: Mapping[str, Any]) -> str:
    distribution = audit["distributions"]
    flight = audit["flight_path"]
    camera = audit["camera"]
    counts = audit["counts"]
    telemetry = audit["telemetry_semantics"]
    return f"""# Post-processing Input Audit

Generated: `{audit['generated_at']}`

## Integrity

- Inference run: `{audit['inference_run']}`
- Status: `{audit['run_status']}`
- Detection observations: **{counts['total_detection_observations']:,}**
- Processed frames: **{counts['processed_frames']}**
- Source hashes match the recorded inference run: **yes**
- The observation count is not a unique-plant count.

## Actual distributions

- Detection confidence p50/p95: `{distribution['confidence']['p50']:.4f}` / `{distribution['confidence']['p95']:.4f}`
- Plant observations per processed frame p50: `{distribution['plant_count_per_processed_frame']['p50']:.0f}`
- Within-frame nearest plant spacing p50: `{distribution['sample_within_frame_nearest_plant_distance_px']['p50']:.2f} px`
- Gimbal pitch range: `{distribution['gimbal_pitch_deg']['min']:.1f}` to `{distribution['gimbal_pitch_deg']['max']:.1f}` degrees
- Relative altitude range: `{distribution['relative_altitude_m']['min']:.3f}` to `{distribution['relative_altitude_m']['max']:.3f}` m
- GPS path length: `{flight['path_length_m']:.2f} m`; endpoint distance `{flight['endpoint_distance_m']:.2f} m`
- Cross-track/along-track PCA ratio: `{flight['pca_cross_track_to_along_track_ratio']:.4f}`

## Camera and telemetry interpretation

- Camera classification: `{camera['classification']}`
- MP4 encoder tag: `{camera['mp4_encoder_tag']}`
- SRT focal/zoom/aperture samples: `{camera['srt_focal_length_values']}` / `{camera['srt_digital_zoom_values']}` / `{camera['srt_aperture_values']}`
- Gimbal near nadir: `{telemetry['gimbal_near_nadir']}`
- Gimbal yaw semantics: `{telemetry['gimbal_yaw_semantics']}`
- RTK status: `{telemetry['rtk_status']}`
- Laser range: `{telemetry['laser_rangefinder_distance']}`
- Relative altitude: `{telemetry['relative_altitude_interpretation']}`

The configured 84-degree FOV is treated as an explicit profile assumption. The 24 mm value is
35-mm-equivalent metadata, not a physical focal length. The almost one-dimensional flight path
makes cross-track georeferencing weakly constrained. WGS84 output must therefore pass a separate
QA gate and may legitimately fall back to local-only coordinates.

## Official references

- https://enterprise.dji.com/matrice-4-series/specs
- https://enterprise.dji.com/geospatial/land-survey
- https://enterprise-insights.dji.com/blog/orthomosaics
"""


def _jsonl_first_keys(path: Path) -> list[str]:
    import json

    with path.open(encoding="utf-8") as handle:
        return list(json.loads(handle.readline()))
