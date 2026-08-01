"""Camera-motion registration and downscaled local survey mosaic construction."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np

from .common import frame_corners, matrix_to_list, quantiles, transform_points, write_csv, write_json


LOGGER = logging.getLogger(__name__)


def _resize_gray(frame: np.ndarray, target_width: int) -> tuple[np.ndarray, float, float]:
    height, width = frame.shape[:2]
    preview_width = min(target_width, width)
    preview_height = max(1, round(height * preview_width / width))
    gray = cv2.cvtColor(cv2.resize(frame, (preview_width, preview_height)), cv2.COLOR_BGR2GRAY)
    return gray, preview_width / width, preview_height / height


def _mutual_ratio_matches(
    descriptors_a: np.ndarray | None,
    descriptors_b: np.ndarray | None,
    ratio: float,
) -> list[cv2.DMatch]:
    if descriptors_a is None or descriptors_b is None or len(descriptors_a) < 2 or len(descriptors_b) < 2:
        return []
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    forward = matcher.knnMatch(descriptors_a, descriptors_b, k=2)
    backward = matcher.knnMatch(descriptors_b, descriptors_a, k=2)
    good_forward = {m.queryIdx: m for m, n in forward if m.distance < ratio * n.distance}
    good_backward = {m.queryIdx: m for m, n in backward if m.distance < ratio * n.distance}
    return [match for query, match in good_forward.items() if good_backward.get(match.trainIdx, None) is not None and good_backward[match.trainIdx].trainIdx == query]


def _affine_metrics(matrix: np.ndarray, source: np.ndarray, target: np.ndarray, mask: np.ndarray) -> dict[str, float | int]:
    inliers = mask.reshape(-1).astype(bool)
    predicted = transform_points(source[inliers], matrix)
    errors = np.linalg.norm(predicted - target[inliers], axis=1)
    linear = matrix[:2, :2]
    determinant = float(np.linalg.det(linear))
    scale = float(math.sqrt(abs(determinant)))
    rotation = float(math.degrees(math.atan2(linear[1, 0], linear[0, 0])))
    return {
        "inlier_count": int(inliers.sum()),
        "inlier_ratio": float(inliers.mean()),
        "reprojection_error_p50_preview_px": float(np.median(errors)),
        "reprojection_error_p95_preview_px": float(np.quantile(errors, 0.95)),
        "determinant": determinant,
        "scale": scale,
        "rotation_deg": rotation,
        "translation_x_preview_px": float(matrix[0, 2]),
        "translation_y_preview_px": float(matrix[1, 2]),
        "perspective_norm": 0.0,
    }


def _gate(metrics: Mapping[str, Any], cfg: Mapping[str, Any]) -> tuple[bool, str]:
    reasons = []
    if metrics["match_count"] < cfg["min_mutual_matches"]:
        reasons.append("too_few_matches")
    if metrics["inlier_count"] < cfg["min_inliers"]:
        reasons.append("too_few_inliers")
    if metrics["inlier_ratio"] < cfg["min_inlier_ratio"]:
        reasons.append("low_inlier_ratio")
    if metrics["reprojection_error_p95_preview_px"] > cfg["max_p95_reprojection_px_preview"]:
        reasons.append("high_reprojection_error")
    if not cfg["min_scale"] <= metrics["scale"] <= cfg["max_scale"]:
        reasons.append("implausible_scale")
    if abs(metrics["rotation_deg"]) > cfg["max_rotation_deg_per_edge"]:
        reasons.append("implausible_rotation")
    return not reasons, "accepted" if not reasons else "|".join(reasons)


def _estimate_sift(
    previous_gray: np.ndarray,
    current_gray: np.ndarray,
    cfg: Mapping[str, Any],
) -> tuple[np.ndarray | None, dict[str, Any], tuple[np.ndarray, np.ndarray] | None]:
    sift = cv2.SIFT_create(nfeatures=int(cfg["max_features"]))
    keypoints_a, descriptors_a = sift.detectAndCompute(previous_gray, None)
    keypoints_b, descriptors_b = sift.detectAndCompute(current_gray, None)
    return _estimate_sift_features(keypoints_a, descriptors_a, keypoints_b, descriptors_b, cfg)


def _estimate_sift_features(
    keypoints_a: Sequence[cv2.KeyPoint],
    descriptors_a: np.ndarray | None,
    keypoints_b: Sequence[cv2.KeyPoint],
    descriptors_b: np.ndarray | None,
    cfg: Mapping[str, Any],
) -> tuple[np.ndarray | None, dict[str, Any], tuple[np.ndarray, np.ndarray] | None]:
    matches = _mutual_ratio_matches(descriptors_a, descriptors_b, float(cfg["ratio_test"]))
    base = {"method": "sift_affine_ransac", "keypoints_previous": len(keypoints_a), "keypoints_current": len(keypoints_b), "match_count": len(matches)}
    if len(matches) < 3:
        return None, {**base, "accepted": False, "reason": "insufficient_correspondences"}, None
    source = np.float64([keypoints_a[m.queryIdx].pt for m in matches])
    target = np.float64([keypoints_b[m.trainIdx].pt for m in matches])
    affine, mask = cv2.estimateAffinePartial2D(
        source,
        target,
        method=cv2.RANSAC,
        ransacReprojThreshold=float(cfg["ransac_reprojection_px_preview"]),
        maxIters=5000,
        confidence=0.999,
        refineIters=25,
    )
    if affine is None or mask is None:
        return None, {**base, "accepted": False, "reason": "estimator_failed"}, (source, target)
    matrix = np.vstack([affine, [0.0, 0.0, 1.0]])
    metrics = {**base, **_affine_metrics(matrix, source, target, mask)}
    accepted, reason = _gate(metrics, cfg)
    return matrix, {**metrics, "accepted": accepted, "reason": reason}, (source, target)


def _estimate_klt(previous_gray: np.ndarray, current_gray: np.ndarray, cfg: Mapping[str, Any]) -> tuple[np.ndarray | None, dict[str, Any]]:
    points = cv2.goodFeaturesToTrack(previous_gray, maxCorners=2500, qualityLevel=0.01, minDistance=8)
    if points is None or len(points) < 3:
        return None, {"method": "klt_affine_ransac", "accepted": False, "reason": "no_features"}
    next_points, status, _error = cv2.calcOpticalFlowPyrLK(previous_gray, current_gray, points, None)
    back_points, back_status, _ = cv2.calcOpticalFlowPyrLK(current_gray, previous_gray, next_points, None)
    valid = status.reshape(-1).astype(bool) & back_status.reshape(-1).astype(bool)
    fb = np.linalg.norm(points.reshape(-1, 2) - back_points.reshape(-1, 2), axis=1)
    valid &= fb < 1.5
    source, target = points.reshape(-1, 2)[valid], next_points.reshape(-1, 2)[valid]
    base = {"method": "klt_affine_ransac", "keypoints_previous": len(points), "keypoints_current": len(next_points), "match_count": len(source)}
    if len(source) < 3:
        return None, {**base, "accepted": False, "reason": "insufficient_correspondences"}
    affine, mask = cv2.estimateAffinePartial2D(source, target, method=cv2.RANSAC, ransacReprojThreshold=float(cfg["ransac_reprojection_px_preview"]), confidence=0.999)
    if affine is None or mask is None:
        return None, {**base, "accepted": False, "reason": "estimator_failed"}
    matrix = np.vstack([affine, [0.0, 0.0, 1.0]])
    metrics = {**base, **_affine_metrics(matrix, source, target, mask)}
    accepted, reason = _gate(metrics, cfg)
    return matrix, {**metrics, "accepted": accepted, "reason": reason}


def _preview_to_full(matrix: np.ndarray, scale_x: float, scale_y: float) -> np.ndarray:
    scale = np.diag([scale_x, scale_y, 1.0])
    return np.linalg.inv(scale) @ matrix @ scale


def register_video_frames(
    video_path: Path,
    frame_indices: Sequence[int],
    output_dir: Path,
    config: Mapping[str, Any],
) -> tuple[dict[int, np.ndarray], dict[int, int], list[dict[str, Any]], dict[str, Any]]:
    """Register each processed raw frame and return full-resolution frame-to-local transforms."""
    cfg = config["registration"]
    wanted = set(int(value) for value in frame_indices)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open source video: {video_path}")
    transforms: dict[int, np.ndarray] = {}
    segments: dict[int, int] = {}
    edges: list[dict[str, Any]] = []
    qa_pairs: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    qa_targets = {int(frame_indices[1]), int(frame_indices[len(frame_indices) // 2]), int(frame_indices[-1])} if len(frame_indices) > 1 else set()
    previous_number: int | None = None
    previous_gray: np.ndarray | None = None
    previous_features: tuple[Sequence[cv2.KeyPoint], np.ndarray | None] | None = None
    sift = cv2.SIFT_create(nfeatures=int(cfg["max_features"]))
    current_global = np.eye(3, dtype=np.float64)
    segment = 0
    frame_number = -1
    decoded_wanted = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frame_number += 1
        if frame_number not in wanted:
            continue
        decoded_wanted += 1
        if decoded_wanted == 1 or decoded_wanted % 25 == 0 or decoded_wanted == len(frame_indices):
            LOGGER.info("Registration frame %d/%d (source frame %d)", decoded_wanted, len(frame_indices), frame_number)
        gray, sx, sy = _resize_gray(frame, int(cfg["preview_width"]))
        current_features = sift.detectAndCompute(gray, None)
        if previous_number is None:
            transforms[frame_number] = current_global.copy()
            segments[frame_number] = segment
        else:
            assert previous_features is not None
            preview_matrix, metrics, _points = _estimate_sift_features(
                previous_features[0], previous_features[1], current_features[0], current_features[1], cfg
            )
            if preview_matrix is None or not metrics.get("accepted", False):
                fallback_matrix, fallback_metrics = _estimate_klt(previous_gray, gray, cfg)
                if fallback_matrix is not None and fallback_metrics.get("accepted", False):
                    preview_matrix, metrics = fallback_matrix, fallback_metrics
                else:
                    metrics["fallback_reason"] = fallback_metrics.get("reason")
            accepted = preview_matrix is not None and bool(metrics.get("accepted", False))
            if accepted:
                pair_full = _preview_to_full(preview_matrix, sx, sy)
                current_global = current_global @ np.linalg.inv(pair_full)
            else:
                segment += 1
                # Registration segments are deliberately disconnected; a large offset avoids false fusion.
                current_global = np.asarray([[1.0, 0.0, segment * 10_000.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
            transforms[frame_number] = current_global.copy()
            segments[frame_number] = segment
            edge = {
                "previous_frame": previous_number,
                "current_frame": frame_number,
                "segment_id": segment,
                **metrics,
                "accepted": accepted,
                "transform_previous_to_current_full": json.dumps(matrix_to_list(_preview_to_full(preview_matrix, sx, sy))) if accepted else "",
                "current_frame_to_local": json.dumps(matrix_to_list(current_global)),
            }
            edges.append(edge)
            if frame_number in qa_targets:
                qa_pairs[frame_number] = (previous_gray.copy(), gray.copy(), preview_matrix.copy() if accepted else np.eye(3))
        previous_number, previous_gray, previous_features = frame_number, gray, current_features
    capture.release()
    missing = sorted(wanted - transforms.keys())
    if missing:
        raise RuntimeError(f"Could not decode processed frames: {missing[:10]}")

    edge_fields = sorted({key for edge in edges for key in edge})
    write_csv(output_dir / "registration_edges.csv", edges, edge_fields)
    accepted_edges = [edge for edge in edges if edge["accepted"]]
    report = {
        "frame_count": len(frame_indices),
        "edge_count": len(edges),
        "accepted_edge_count": len(accepted_edges),
        "transform_failure_count": len(edges) - len(accepted_edges),
        "registration_segment_count": len(set(segments.values())),
        "methods": {method: sum(edge.get("method") == method for edge in edges) for method in sorted({str(edge.get("method")) for edge in edges})},
        "reprojection_error_p50_preview_px": quantiles([float(edge["reprojection_error_p50_preview_px"]) for edge in accepted_edges]),
        "reprojection_error_p95_preview_px": quantiles([float(edge["reprojection_error_p95_preview_px"]) for edge in accepted_edges]),
        "inlier_ratio": quantiles([float(edge["inlier_ratio"]) for edge in accepted_edges]),
        "scale": quantiles([float(edge["scale"]) for edge in accepted_edges]),
        "rotation_deg": quantiles([float(edge["rotation_deg"]) for edge in accepted_edges]),
        "determinant": quantiles([float(edge["determinant"]) for edge in accepted_edges]),
        "failed_edges": [{"previous_frame": edge["previous_frame"], "current_frame": edge["current_frame"], "reason": edge.get("reason")} for edge in edges if not edge["accepted"]],
        "coordinate_convention": "3x3 matrices map full-resolution raw-frame pixels to arbitrary local map pixels",
    }
    write_json(output_dir / "registration_report.json", report)
    _write_pairwise_qa(output_dir / "registration_qa", qa_pairs)
    return transforms, segments, edges, report


def _write_pairwise_qa(directory: Path, pairs: Mapping[int, tuple[np.ndarray, np.ndarray, np.ndarray]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if not pairs:
        return
    keys = sorted(pairs)
    chosen = sorted(set([keys[0], keys[len(keys) // 2], keys[-1]]))
    for frame_number in chosen:
        previous, current, matrix = pairs[frame_number]
        warped = cv2.warpPerspective(previous, matrix, (current.shape[1], current.shape[0]))
        checker = cv2.cvtColor(current, cv2.COLOR_GRAY2BGR)
        block = 48
        for y in range(0, current.shape[0], block):
            for x in range(0, current.shape[1], block):
                if (x // block + y // block) % 2 == 0:
                    checker[y : y + block, x : x + block] = cv2.cvtColor(warped[y : y + block, x : x + block], cv2.COLOR_GRAY2BGR)
        cv2.imwrite(str(directory / f"pair_to_frame_{frame_number:06d}_checkerboard.jpg"), checker)
        overlay = cv2.addWeighted(cv2.cvtColor(warped, cv2.COLOR_GRAY2BGR), 0.5, cv2.cvtColor(current, cv2.COLOR_GRAY2BGR), 0.5, 0)
        cv2.imwrite(str(directory / f"pair_to_frame_{frame_number:06d}_overlay.jpg"), overlay)


def build_mosaic(
    video_path: Path,
    frame_indices: Sequence[int],
    transforms: Mapping[int, np.ndarray],
    segments: Mapping[int, int],
    output_dir: Path,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a bounded-memory preview mosaic and local footprint/coverage products."""
    width = int(config["video"]["width"])
    height = int(config["video"]["height"])
    roi_height = int(config["coverage"]["roi_height"])
    corners = frame_corners(width, height, roi_height)
    footprints = {number: transform_points(corners, transforms[number]) for number in frame_indices}
    all_points = np.vstack(list(footprints.values()))
    minimum, maximum = all_points.min(axis=0), all_points.max(axis=0)
    span = np.maximum(maximum - minimum, 1.0)
    mosaic_cfg = config["mosaic"]
    scale = min(float(mosaic_cfg["max_preview_width"]) / span[0], float(mosaic_cfg["max_preview_height"]) / span[1], 1.0)
    preview_width = int(math.ceil(span[0] * scale)) + 20
    preview_height = int(math.ceil(span[1] * scale)) + 20
    local_to_preview = np.asarray([[scale, 0.0, 10.0 - scale * minimum[0]], [0.0, scale, 10.0 - scale * minimum[1]], [0.0, 0.0, 1.0]])

    centers = {number: transform_points(np.asarray([[width / 2, height / 2]]), transforms[number])[0] for number in frame_indices}
    keyframes = [frame_indices[0]]
    for number in frame_indices[1:]:
        if segments[number] != segments[keyframes[-1]] or np.linalg.norm(centers[number] - centers[keyframes[-1]]) >= float(mosaic_cfg["minimum_keyframe_center_displacement_px"]):
            keyframes.append(number)
    if keyframes[-1] != frame_indices[-1]:
        keyframes.append(frame_indices[-1])

    accumulation = np.zeros((preview_height, preview_width, 3), dtype=np.float32)
    weights = np.zeros((preview_height, preview_width), dtype=np.float32)
    coverage = np.zeros((preview_height, preview_width), dtype=np.uint16)
    wanted = set(keyframes)
    capture = cv2.VideoCapture(str(video_path))
    frame_number = -1
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frame_number += 1
        if frame_number not in wanted:
            continue
        preview_width_source = min(int(config["registration"]["preview_width"]), width)
        preview_height_source = round(height * preview_width_source / width)
        small = cv2.resize(frame, (preview_width_source, preview_height_source))
        preview_to_full = np.diag([width / preview_width_source, height / preview_height_source, 1.0])
        warp = local_to_preview @ transforms[frame_number] @ preview_to_full
        warped = cv2.warpPerspective(small, warp, (preview_width, preview_height))
        valid = cv2.warpPerspective(np.ones(small.shape[:2], dtype=np.uint8), warp, (preview_width, preview_height), flags=cv2.INTER_NEAREST).astype(bool)
        accumulation[valid] += warped[valid]
        weights[valid] += 1.0
    capture.release()
    for number in frame_indices:
        polygon = np.round(transform_points(footprints[number], local_to_preview)).astype(np.int32)
        cv2.fillPoly(coverage, [polygon], int(min(65535, int(coverage.max()) + 1)))
    # fillPoly with a scalar cannot increment overlaps, so compute increments explicitly.
    coverage.fill(0)
    mask = np.zeros_like(coverage, dtype=np.uint8)
    for number in frame_indices:
        mask.fill(0)
        polygon = np.round(transform_points(footprints[number], local_to_preview)).astype(np.int32)
        cv2.fillPoly(mask, [polygon], 1)
        coverage += mask.astype(np.uint16)
    mosaic = np.zeros_like(accumulation, dtype=np.uint8)
    nonzero = weights > 0
    mosaic[nonzero] = np.clip(accumulation[nonzero] / weights[nonzero, None], 0, 255).astype(np.uint8)
    cv2.imwrite(str(output_dir / "survey_mosaic_preview.png"), mosaic)
    coverage_dir = output_dir / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    normalized = np.uint8(np.clip(coverage / max(1, int(coverage.max())) * 255, 0, 255))
    cv2.imwrite(str(coverage_dir / "coverage_map.png"), cv2.applyColorMap(normalized, cv2.COLORMAP_VIRIDIS))
    np.savez_compressed(coverage_dir / "coverage_counts.npz", coverage=coverage)

    features = []
    for number in frame_indices:
        coordinates = footprints[number].tolist()
        coordinates.append(coordinates[0])
        features.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [coordinates]}, "properties": {"frame_index": int(number), "segment_id": int(segments[number]), "coordinate_space": "registered_local_pixels"}})
    write_json(output_dir / "frame_footprints_local.geojson", {"type": "FeatureCollection", "coordinate_space": "registered_local_pixels", "features": features})
    payload = {
        "coordinate_space": "registered_local_pixels",
        "frame_to_local": {str(number): matrix_to_list(transforms[number]) for number in frame_indices},
        "local_to_preview": matrix_to_list(local_to_preview),
        "preview_to_local": matrix_to_list(np.linalg.inv(local_to_preview)),
        "preview_size": [preview_width, preview_height],
        "full_frame_size": [width, height],
        "analysis_roi_height": roi_height,
        "local_bounds": [float(minimum[0]), float(minimum[1]), float(maximum[0]), float(maximum[1])],
        "mosaic_keyframes": [int(value) for value in keyframes],
        "coverage_max_processed_frames": int(coverage.max()),
    }
    write_json(output_dir / "mosaic_transform.json", payload)
    return {**payload, "coverage_array": coverage, "mosaic_image": mosaic, "footprints": footprints}
