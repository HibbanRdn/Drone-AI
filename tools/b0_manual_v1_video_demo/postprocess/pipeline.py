"""End-to-end orchestration for prepare and manual-polygon gap analysis."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .audit import audit_inference_run, audit_markdown
from .common import list_to_matrix, read_json, sha256_file, utc_now_iso, write_json
from .config import merged_config
from .fusion import fuse_detections, write_unique_plant_overlay
from .gaps import analyze_gaps
from .georef import evaluate_and_apply_georeference
from .geometry_sanitize import (
    sanitize_feature_collection,
    write_geometry_qa_report,
)
from .plots import ensure_empty_plots
from .registration import build_mosaic, register_video_frames


LOGGER = logging.getLogger(__name__)


def _default_output(inference_run: Path, smoke: bool = False) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = "postprocess_smoke" if smoke else "postprocess_run"
    return inference_run.parent / f"{prefix}_{timestamp}"


def prepare(
    inference_run: Path,
    output_dir: Path | None = None,
    overrides: Mapping[str, Any] | None = None,
    max_frames: int | None = None,
    smoke: bool = False,
) -> Path:
    inference_run = inference_run.resolve()
    output_dir = (output_dir or _default_output(inference_run, smoke)).resolve()
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing postprocess run: {output_dir}")
    output_dir.mkdir(parents=True)
    log_path = output_dir / "runtime.log"
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(file_handler)
    try:
        LOGGER.info("Auditing inference run %s", inference_run)
        config = merged_config(overrides)
        audit = audit_inference_run(inference_run, config)
        stream = audit["video_probe"]["streams"][0]
        config["video"] = {"width": int(stream["width"]), "height": int(stream["height"]), "frame_count": int(stream["nb_frames"]), "fps": stream["avg_frame_rate"]}
        frame_summary = pd.read_csv(inference_run / "frame_summary.csv")
        frame_indices = frame_summary["frame_index"].astype(int).tolist()
        if max_frames is not None:
            if max_frames < 2:
                raise ValueError("--max-frames must be at least 2")
            count = min(max_frames, len(frame_indices))
            if smoke and count >= 6:
                # Two contiguous windows exercise real pairwise overlap at the start and middle.
                first_count = count // 2
                middle_count = count - first_count
                middle_start = max(first_count, len(frame_indices) // 2 - middle_count // 2)
                positions = list(range(first_count)) + list(range(middle_start, min(len(frame_indices), middle_start + middle_count)))
            else:
                positions = list(range(count))
            frame_indices = [frame_indices[index] for index in sorted(set(positions))]
        config["run"] = {
            "created_at": utc_now_iso(),
            "inference_run": str(inference_run),
            "output_dir": str(output_dir),
            "processed_frame_indices": frame_indices,
            "smoke": smoke,
            "max_frames": max_frames,
        }
        write_json(output_dir / "postprocess_config.json", config)
        write_json(output_dir / "input_audit.json", audit)
        (output_dir / "input_audit.md").write_text(audit_markdown(audit), encoding="utf-8")

        video_path = Path(audit["recorded_sources"]["video"])
        LOGGER.info("Registering %d raw processed frames", len(frame_indices))
        transforms, segments, edges, registration_report = register_video_frames(video_path, frame_indices, output_dir, config)
        LOGGER.info("Building bounded-memory survey mosaic")
        mosaic_info = build_mosaic(video_path, frame_indices, transforms, segments, output_dir, config)
        LOGGER.info("Fusing detection observations in registered local coordinates")
        unique, observations, fusion_report = fuse_detections(
            inference_run / "detections.csv",
            frame_indices,
            transforms,
            segments,
            mosaic_info["footprints"],
            output_dir,
            audit,
            registration_report,
            config,
        )
        LOGGER.info("Evaluating conservative video/SRT georeference")
        geo_report = evaluate_and_apply_georeference(
            inference_run / "telemetry_sync.csv", frame_indices, transforms, output_dir / "unique_plants.csv", output_dir, audit, config
        )
        unique = pd.read_csv(output_dir / "unique_plants.csv")
        write_unique_plant_overlay(output_dir, mosaic_info["mosaic_image"], list_to_matrix(mosaic_info["local_to_preview"]), unique)
        ensure_empty_plots(output_dir / "plots_local.geojson")
        (output_dir / "gap_evidence").mkdir(exist_ok=True)

        source_hashes_after = {key: sha256_file(Path(path)) for key, path in audit["recorded_sources"].items()}
        source_unchanged = source_hashes_after == audit["source_hashes_current"]
        if not source_unchanged:
            raise RuntimeError("A protected source changed during post-processing")
        summary = {
            "status": "prepare_complete_manual_polygon_pending",
            "created_at": config["run"]["created_at"],
            "completed_at": utc_now_iso(),
            "inference_run": str(inference_run),
            "total_detection_observations_in_source_run": audit["counts"]["total_detection_observations"],
            "detection_observations_processed": fusion_report["total_detection_observations"],
            "unique_plant_candidates": fusion_report["unique_plant_candidates"],
            "confirmed_unique_plants": fusion_report["confirmed_unique_plants"],
            "low_support_unique_plants": fusion_report["low_support_unique_plants"],
            "registration": registration_report,
            "fusion": fusion_report,
            "georeference": geo_report,
            "manual_plot_status": "pending_user_polygon",
            "source_hashes_after": source_hashes_after,
            "protected_sources_unchanged": source_unchanged,
            "old_inference_run_modified": False,
            "training_run": False,
            "yolo_inference_rerun": False,
        }
        write_json(output_dir / "postprocess_summary.json", summary)
        _write_qa_report(output_dir, summary)
        LOGGER.info("Prepare complete: %s", output_dir)
        return output_dir
    except Exception:
        LOGGER.exception("Postprocess prepare failed")
        write_json(output_dir / "failure.json", {"status": "failed", "failed_at": utc_now_iso()})
        raise
    finally:
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()


def run_gap_analysis(postprocess_run: Path, plots_path: Path | None = None) -> dict[str, Any]:
    postprocess_run = postprocess_run.resolve()
    config = read_json(postprocess_run / "postprocess_config.json")
    plots_path = (plots_path or postprocess_run / "plots_local.geojson").resolve()
    geometry_config = config.get("geometry_sanitation", {})
    sanitized_plots, geometry_pass = sanitize_feature_collection(
        read_json(plots_path),
        minimum_component_area=float(
            geometry_config.get("minimum_component_area_local_px2", 1.0)
        ),
        minimum_hole_area=float(
            geometry_config.get("minimum_hole_area_local_px2", 1.0)
        ),
        simplify_tolerance=0.0,
        material_area_change_fraction=float(
            geometry_config.get("material_area_change_fraction", 0.02)
        ),
    )
    sanitized_path = postprocess_run / "analysis_polygons_sanitized.geojson"
    sanitized_plots["sanitized_from"] = str(plots_path)
    write_json(sanitized_path, sanitized_plots)
    geometry_qa = write_geometry_qa_report(
        postprocess_run / "polygon_geometry_qa.json",
        geometry_pass,
        context="defensive_pre_gap_analysis",
    )
    report = analyze_gaps(postprocess_run, sanitized_path, config)
    report["polygon_geometry_qa"] = {
        "path": str(postprocess_run / "polygon_geometry_qa.json"),
        "status": geometry_qa["status"],
        **geometry_qa["summary"],
    }
    write_json(postprocess_run / "gap_analysis_report.json", report)
    summary = read_json(postprocess_run / "postprocess_summary.json")
    summary["status"] = (
        "gap_analysis_complete_with_warnings_candidates_require_manual_review"
        if geometry_qa["summary"]["needs_review"]
        else "gap_analysis_complete_candidates_require_manual_review"
    )
    plots_payload = sanitized_plots
    sources = {
        str(feature.get("properties", {}).get("source", ""))
        for feature in plots_payload.get("features", [])
    }
    if sources == {"ai_assisted_visual_mosaic_unique_plants_coverage"}:
        summary["manual_plot_status"] = "ai_assisted_polygon_loaded"
    elif sources == {"segmentation_plantable_area_union_closing_3_temporal_majority"}:
        summary["manual_plot_status"] = "segmentation_derived_area_loaded"
    else:
        summary["manual_plot_status"] = "user_polygon_loaded"
    summary["plots_source_path"] = str(plots_path)
    summary["plots_path"] = str(sanitized_path)
    summary["polygon_geometry_qa"] = report["polygon_geometry_qa"]
    summary["gap_analysis"] = report
    summary["completed_at"] = utc_now_iso()
    write_json(postprocess_run / "postprocess_summary.json", summary)
    _write_qa_report(postprocess_run, summary)
    return report


def _write_qa_report(output_dir: Path, summary: Mapping[str, Any]) -> None:
    registration = summary["registration"]
    fusion = summary["fusion"]
    geo = summary["georeference"]
    gap = summary.get("gap_analysis")
    text = f"""# Post-processing QA Report

## State

- Status: `{summary['status']}`
- Plot input status: `{summary['manual_plot_status']}`
- Protected sources unchanged: `{summary['protected_sources_unchanged']}`
- YOLO inference rerun: `{summary['yolo_inference_rerun']}`
- Training run: `{summary['training_run']}`

## Registration

- Frames / edges: {registration['frame_count']} / {registration['edge_count']}
- Failed transforms: {registration['transform_failure_count']}
- Registration segments: {registration['registration_segment_count']}
- Edge p50 reprojection-error distribution p50: {registration['reprojection_error_p50_preview_px']['p50']}
- Edge p95 reprojection-error distribution p95: {registration['reprojection_error_p95_preview_px']['p95']}

## Temporal fusion

- Detection observations processed: {fusion['total_detection_observations']:,}
- Unique plant candidates: {fusion['unique_plant_candidates']:,}
- Confirmed unique plants: {fusion['confirmed_unique_plants']:,}
- Low-support candidates: {fusion['low_support_unique_plants']:,}
- Association radius: {fusion['association_radius_local_px']:.3f} local px
- Radius fraction of median plant spacing: {fusion['association_radius_local_px'] / fusion['association_radius_basis']['median_plant_spacing_local_px']:.3f}
- Same-frame collision invariant: {fusion['invariants']['no_two_observations_same_frame_per_plant']}

## Georeferencing

- Status: `{geo['status']}`
- Method: `{geo['method']}`
- P95 anchor residual: {geo['alignment_residual_m']['p95']} m
- Cross/along path ratio: {geo['flight_cross_track_to_along_track_ratio']}
- Visual/GPS step direction cosine p50: {geo.get('visual_to_gps_step_direction_cosine', {}).get('p50')}
- Visual/GPS positive-direction fraction: {geo.get('visual_to_gps_positive_direction_fraction')}
- Failure reasons: {', '.join(geo['failure_reasons']) if geo['failure_reasons'] else 'none'}

Latitude/longitude from video/SRT is approximate, never survey-grade. When QA is invalid,
all plant and gap WGS84 fields remain null and local coordinates are authoritative.

## Plot and gap status

{('No real polygon has been saved. Gap analysis has intentionally not run.' if gap is None else f"Plots: {gap['plot_count']}; rows: {gap['rows']}; expected points: {gap['expected_planting_points']}; missing plants: {gap['estimated_missing_plants']}; gap groups: {gap['gap_count']}.")}

## Visual QA files

- `registration_qa/`: early, middle, and late raw-frame checkerboard/overlay pairs.
- `survey_mosaic_preview.png`: downscaled local survey mosaic.
- `unique_plants_mosaic_overlay.png`: support-colored unique-plant candidates.
- `coverage/coverage_map.png`: processed-frame visibility opportunity.
- `gap_map_preview.png` and `gap_evidence/`: created after a real manual polygon is analyzed.
"""
    (output_dir / "qa_report.md").write_text(text, encoding="utf-8")
