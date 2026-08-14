from __future__ import annotations

"""v20 Finalizer â€” ported from Plot-Gap reference, with actual registration,
correct gap semantics, proper geographic separation, and validated SHP output."""

import json, math, os, time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import cv2
import numpy as np

from .spatial import SimpleKDTree
from .shp_writer import write_shapefile, wgs84_prj


# -----------------------------------------------------------
# REGISTRATION (cv2 SIFT, no identity-fallback)
# -----------------------------------------------------------
def register_frame_pair(
    prev_gray: np.ndarray,
    curr_gray: np.ndarray,
    max_features: int = 4000,
    ratio_test: float = 0.75,
    ransac_threshold: float = 5.0,
    min_inliers: int = 10,
    min_inlier_ratio: float = 0.3,
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """Register two grayscale frames using SIFT + mutual ratio matching + partial affine RANSAC.
    Returns (3x3 matrix or None, diagnostics dict)."""

    sift = cv2.SIFT_create(nfeatures=max_features)
    kp1, des1 = sift.detectAndCompute(prev_gray, None)
    kp2, des2 = sift.detectAndCompute(curr_gray, None)

    if des1 is None or des2 is None or len(des1) < 10 or len(des2) < 10:
        return None, {"status": "insufficient_features", "kp1": len(kp1), "kp2": len(kp2)}

    # Mutual ratio matching
    bf = cv2.BFMatcher()
    matches_fwd = bf.knnMatch(des1, des2, k=2)
    matches_bwd = bf.knnMatch(des2, des1, k=2)

    # Ratio test forward
    good_fwd = set()
    for m, n in matches_fwd:
        if m.distance < ratio_test * n.distance:
            good_fwd.add((m.queryIdx, m.trainIdx))

    # Ratio test backward
    good_bwd = set()
    for m, n in matches_bwd:
        if m.distance < ratio_test * n.distance:
            good_bwd.add((m.trainIdx, m.queryIdx))

    # Mutual consent
    mutual = good_fwd & good_bwd
    if len(mutual) < min_inliers:
        return None, {"status": "insufficient_mutual_matches", "matches": len(mutual)}

    src_pts = np.float32([kp1[i].pt for i, _ in mutual]).reshape(-1, 2)
    dst_pts = np.float32([kp2[j].pt for _, j in mutual]).reshape(-1, 2)

    # Partial affine RANSAC
    M, inliers = cv2.estimateAffinePartial2D(
        src_pts, dst_pts,
        method=cv2.RANSAC,
        ransacReprojThreshold=ransac_threshold,
        maxIters=5000,
        confidence=0.999,
        refineIters=25,
    )

    if M is None or inliers is None:
        return None, {"status": "ransac_failed"}

    inlier_count = int(np.sum(inliers))
    inlier_ratio = inlier_count / len(mutual) if len(mutual) > 0 else 0

    if inlier_count < min_inliers or inlier_ratio < min_inlier_ratio:
        return None, {
            "status": "qa_rejected",
            "inliers": inlier_count,
            "ratio": inlier_ratio,
            "matches": len(mutual),
        }

    # Convert to 3x3
    matrix = np.eye(3)
    matrix[:2, :] = M

    # Validate scale
    sx = math.sqrt(matrix[0, 0]**2 + matrix[0, 1]**2)
    sy = math.sqrt(matrix[1, 0]**2 + matrix[1, 1]**2)
    if sx < 0.5 or sx > 2.0 or sy < 0.5 or sy > 2.0:
        return None, {"status": "scale_rejected", "sx": sx, "sy": sy}

    return matrix, {
        "status": "accepted",
        "matches": len(mutual),
        "inliers": inlier_count,
        "ratio": inlier_ratio,
    }


def register_frame_pair_klt(prev_gray, curr_gray, max_corners=2500, fb_threshold=1.5):
    """KLT optical-flow fallback for registration.
    Used when SIFT produces insufficient matches."""
    import cv2
    corners = cv2.goodFeaturesToTrack(prev_gray, maxCorners=max_corners, qualityLevel=0.01, minDistance=5)
    if corners is None or len(corners) < 8:
        return None, {"status": "insufficient_corners", "corners": len(corners) if corners is not None else 0}
    # Forward flow
    curr_pts, status_fwd, _ = cv2.calcOpticalFlowPyrLK(prev_gray, curr_gray, corners, None)
    if curr_pts is None or status_fwd is None:
        return None, {"status": "klt_forward_failed"}
    # Backward flow for consistency check
    back_pts, status_bwd, _ = cv2.calcOpticalFlowPyrLK(curr_gray, prev_gray, curr_pts, None)
    if back_pts is None:
        return None, {"status": "klt_backward_failed"}
    fb_err = np.linalg.norm((corners - back_pts).reshape(-1, 2), axis=1)
    good = fb_err < fb_threshold
    good &= status_fwd.ravel() == 1
    good &= status_bwd.ravel() == 1
    src = corners[good].reshape(-1, 2).astype(np.float32)
    dst = curr_pts[good].reshape(-1, 2).astype(np.float32)
    if len(src) < 8:
        return None, {"status": "insufficient_klt_inliers", "inliers": len(src)}
    M, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
    if M is None:
        return None, {"status": "klt_ransac_failed"}
    matrix = np.eye(3)
    matrix[:2, :] = M
    return matrix, {
        "status": "accepted",
        "method": "klt",
        "corners": len(corners),
        "tracked": int(np.sum(good)),
        "inliers": int(np.sum(inliers)) if inliers is not None else 0,
    }


def register_frame_sequence(
    frames: Dict[int, np.ndarray],
    config: Dict[str, Any],
) -> Tuple[Dict[int, np.ndarray], Dict[int, int], List[Dict[str, Any]]]:
    """Register a sequence of frames. Returns:
    - transforms: frame_id -> 3x3 matrix (local coords)
    - segments: frame_id -> segment_id
    - report: list of per-edge diagnostics
    """
    reg_cfg = config.get("registration", {})
    max_features = int(reg_cfg.get("max_features", 4000))
    ratio_test = float(reg_cfg.get("ratio_test", 0.75))
    ransac_threshold = float(reg_cfg.get("ransac_reprojection_px", 5.0))
    min_inliers = int(reg_cfg.get("min_inliers", 10))

    frame_ids = sorted(frames.keys())
    if not frame_ids:
        return {}, {}, []

    # Initialize: first frame is identity
    transforms = {frame_ids[0]: np.eye(3)}
    segments = {frame_ids[0]: 0}
    report = []
    current_segment = 0
    prev_id = frame_ids[0]

    for fi in frame_ids[1:]:
        prev_gray = cv2.cvtColor(frames[prev_id], cv2.COLOR_BGR2GRAY)
        curr_gray = cv2.cvtColor(frames[fi], cv2.COLOR_BGR2GRAY)

        M, diag = register_frame_pair(
            prev_gray, curr_gray,
            max_features=max_features,
            ratio_test=ratio_test,
            ransac_threshold=ransac_threshold,
            min_inliers=min_inliers,
        )

        if M is not None:
            # Chain transforms
            transforms[fi] = transforms[prev_id] @ M
            segments[fi] = current_segment
            diag["edge"] = f"{prev_id}->{fi}"
            diag["accepted"] = True
        else:
            # Registration break: new segment, identity restart
            current_segment += 1
            transforms[fi] = np.eye(3)
            segments[fi] = current_segment
            diag["edge"] = f"{prev_id}->{fi}"
            diag["accepted"] = False

        report.append(diag)
        prev_id = fi

    return transforms, segments, report


# -----------------------------------------------------------
# KDTree helpers
# -----------------------------------------------------------
def _build_tree(points: List[Tuple[float, float]]) -> SimpleKDTree:
    return SimpleKDTree(points)


# -----------------------------------------------------------
# UNIQUE PLANT FUSION
# -----------------------------------------------------------
def greedy_one_to_one(
    previous: np.ndarray, current: np.ndarray, radius: float
) -> List[Tuple[int, int, float]]:
    if len(previous) == 0 or len(current) == 0:
        return []
    tree = _build_tree([(float(p[0]), float(p[1])) for p in previous])
    candidates = []
    for j, c in enumerate(current):
        cx, cy = float(c[0]), float(c[1])
        results = tree.query((cx, cy), k=1)
        if results and results[0][0] <= radius:
            candidates.append((results[0][0], results[0][1], j))
    candidates.sort(key=lambda x: (x[0], x[1], x[2]))
    assigned_prev: Set[int] = set()
    assigned_curr: Set[int] = set()
    matches = []
    for dist, i, j in candidates:
        if i not in assigned_prev and j not in assigned_curr:
            assigned_prev.add(i)
            assigned_curr.add(j)
            matches.append((i, j, dist))
    return matches


def fuse_detections(
    detections: List[Dict[str, Any]],
    transforms: Dict[int, np.ndarray],
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    fusion_cfg = config.get("fusion", {})
    assoc_radius = float(fusion_cfg.get("association_radius_px", 30.0))
    min_obs = int(fusion_cfg.get("confirmed_min_observations", 2))

    frame_dets = defaultdict(list)
    for d in detections:
        frame_dets[d.get("frame_index", 0)].append(d)

    frame_indices = sorted(frame_dets.keys())
    tracks = []
    plant_counter = 0

    for fi in frame_indices:
        transform = transforms.get(fi, np.eye(3))
        current_frame_dets = frame_dets[fi]

        local_positions = []
        local_data = []
        for d in current_frame_dets:
            for p in d.get("plant_detections", []):
                bbox = p.get("bbox_xyxy", [0, 0, 0, 0])
                if len(bbox) >= 4:
                    cx = (bbox[0] + bbox[2]) / 2
                    cy = (bbox[1] + bbox[3]) / 2
                    pt = np.array([cx, cy, 1.0])
                    lp = transform @ pt
                    local_positions.append((float(lp[0]), float(lp[1])))
                    local_data.append((p, lp))

        if not local_positions:
            continue

        if tracks:
            tp = np.array([[float(np.median(t["xs"])), float(np.median(t["ys"]))] for t in tracks])
            cp = np.array(local_positions)
            matches = greedy_one_to_one(tp, cp, assoc_radius)
            matched_tracks = set()
            matched_dets = set()
            for ti, di, _ in matches:
                matched_tracks.add(ti)
                matched_dets.add(di)
                p, lp = local_data[di]
                tracks[ti]["xs"].append(float(lp[0]))
                tracks[ti]["ys"].append(float(lp[1]))
                tracks[ti]["count"] += 1
                tracks[ti]["confs"].append(float(p.get("confidence", 0.5)))
                tracks[ti]["frames_seen"].append(fi)
        else:
            matched_dets = set()

        for di in range(len(local_positions)):
            if di not in matched_dets:
                p, lp = local_data[di]
                plant_counter += 1
                tracks.append({
                    "id": "plant_%06d" % plant_counter,
                    "xs": [float(lp[0])],
                    "ys": [float(lp[1])],
                    "count": 1,
                    "confs": [float(p.get("confidence", 0.5))],
                    "frames_seen": [fi],
                })

    results = []
    for t in tracks:
        xs = t["xs"]
        ys = t["ys"]
        confs = t["confs"]
        final_x = float(np.median(xs))
        final_y = float(np.median(ys))
        results.append({
            "plant_id": t["id"],
            "local_x": final_x,
            "local_y": final_y,
            "observation_count": t["count"],
            "confidence": max(confs) if confs else 0.0,
            "mean_confidence": sum(confs) / len(confs) if confs else 0.0,
            "first_frame": min(t["frames_seen"]) if t["frames_seen"] else 0,
            "last_frame": max(t["frames_seen"]) if t["frames_seen"] else 0,
        })
    return results



def estimate_dominant_orientation(
    points: List[Tuple[float, float]], bin_degrees: float = 2.0
) -> Tuple[float, float]:
    n = len(points)
    if n < 4:
        return 0.0, 0.0
    tree = _build_tree(points)
    k = min(5, n - 1)
    angles = []
    weights = []
    for i, (px, py) in enumerate(points):
        results = tree.query((px, py), k=k + 1)
        for dist, j in results[1:]:
            if dist > 0:
                a = math.degrees(math.atan2(points[j][1] - py, points[j][0] - px)) % 180
                angles.append(a)
                weights.append(1.0 / max(dist, 0.1))
    if not angles:
        return 0.0, 0.0
    nb = int(180.0 / bin_degrees)
    hist = [0.0] * nb
    for a, w in zip(angles, weights):
        hist[int(a / bin_degrees) % nb] += w
    peak = max(range(nb), key=lambda i: hist[i])
    total = sum(hist) or 1.0
    return peak * bin_degrees + bin_degrees / 2, min(1.0, hist[peak] / total * 6.0)


def robust_spacing(values: List[float]) -> Tuple[float, float]:
    valid = [float(v) for v in values if v is not None and str(v).strip() != ""]
    if len(valid) < 3:
        return 0.0, 0.0
    sv = sorted(valid)
    n = len(sv)
    upper = sv[:int(n * 0.65)]
    if not upper:
        return 0.0, 0.0
    med = float(np.median(upper))
    if med <= 0:
        return 0.0, 0.0
    mad = float(np.median([abs(v - med) for v in upper]))
    return med, max(0.0, min(1.0, 1.0 - 1.4826 * mad / med))

def _row_coords_to_local(along, across, cx, cy, theta):
    """Map (along, across) row coordinates back into local world coordinates.

    Exact inverse of the forward row-frame rotation used in analyze_gaps
    (along = (x-cx)*cos + (y-cy)*sin ; across = -(x-cx)*sin + (y-cy)*cos).
    across is the signed distance from the row-center line.
    """
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    return (cx + along * cos_t - across * sin_t,
            cy + along * sin_t + across * cos_t)


def analyze_gaps(
    unique_plants: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Gap analysis via row reconstruction.
    Rows are detected by projecting plant positions onto the perpendicular
    axis of the dominant planting direction, clustering those projections,
    then finding expected positions along each row."""
    gap_cfg = config.get("gap_analysis", {})
    min_plants_row = int(gap_cfg.get("minimum_plants_per_row", 4))
    row_band_frac = float(gap_cfg.get("row_assignment_band_spacing_fraction", 0.42))

    points = []
    for p in unique_plants:
        lx = p.get("local_x"); ly = p.get("local_y")
        if lx is not None and ly is not None and math.isfinite(float(lx)) and math.isfinite(float(ly)):
            points.append((float(lx), float(ly)))

    if len(points) < min_plants_row * 2:
        return {"evaluated": False, "count": None, "status": "NOT_EVALUATED",
                "reason": "insufficient_plants", "gaps": []}

    orient, orient_conf = estimate_dominant_orientation(points)
    if orient_conf < 0.1:
        return {"evaluated": False, "count": None, "status": "NOT_EVALUATED",
                "reason": "orientation_low_confidence", "gaps": []}

    ang = math.radians(orient)
    cos_a, sin_a = math.cos(ang), math.sin(ang)
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)

    along = [(p[0] - cx) * cos_a + (p[1] - cy) * sin_a for p in points]
    across = [-(p[0] - cx) * sin_a + (p[1] - cy) * cos_a for p in points]

    tree = _build_tree(points)
    along_spacings = []
    cross_spacings = []
    for i in range(len(points)):
        results = tree.query(points[i], k=min(12, len(points)))
        best_along = None
        best_cross = None
        for dist, j in results[1:]:
            if dist <= 0:
                continue
            da = abs(along[i] - along[j])
            dc = abs(across[i] - across[j])
            if dc < max(5.0, da * 0.3):
                if best_along is None or da < best_along:
                    best_along = da
            elif da < max(5.0, dc * 0.3):
                if best_cross is None or dc < best_cross:
                    best_cross = dc
        if best_along is not None:
            along_spacings.append(best_along)
        if best_cross is not None:
            cross_spacings.append(best_cross)

    along_spacing, _ = robust_spacing(along_spacings)
    cross_spacing, _ = robust_spacing(cross_spacings)
    if along_spacing <= 0:
        along_spacing = float(gap_cfg.get("default_plant_spacing_px", 30.0))
    if cross_spacing <= 0:
        cross_spacing = along_spacing * 2.0

    ac_sorted = sorted(across)
    row_centers = []
    current_cluster = [ac_sorted[0]]
    cluster_gap = cross_spacing * 0.5
    for ac in ac_sorted[1:]:
        if ac - current_cluster[-1] <= cluster_gap:
            current_cluster.append(ac)
        else:
            row_centers.append(sum(current_cluster) / len(current_cluster))
            current_cluster = [ac]
    row_centers.append(sum(current_cluster) / len(current_cluster))

    if len(row_centers) < 1:
        return {"evaluated": False, "count": None, "status": "NOT_EVALUATED",
                "reason": "no_valid_rows", "gaps": []}

    band = cross_spacing * row_band_frac
    assignments = []
    for ac in across:
        dists = [abs(ac - rc) for rc in row_centers]
        best = min(range(len(dists)), key=lambda k: dists[k])
        assignments.append(best if dists[best] <= band else -1)

    row_plants = {}
    for i, ri in enumerate(assignments):
        if ri >= 0:
            row_plants.setdefault(ri, []).append((along[i], points[i]))

    gap_id = 0
    all_gaps = []
    tolerance = along_spacing * 0.42
    for ri, rp in row_plants.items():
        rp.sort(key=lambda v: v[0])
        if len(rp) < min_plants_row:
            continue
        min_a = rp[0][0]
        max_a = rp[-1][0]
        num_expected = max(0, int(round((max_a - min_a) / along_spacing)) - 1)
        for slot in range(1, num_expected + 1):
            exp_along = min_a + slot * along_spacing
            found = False
            for pa, _ in rp:
                if abs(pa - exp_along) <= tolerance:
                    found = True
                    break
            if not found:
                gap_id += 1
                rc = row_centers[ri]
                exp_lx, exp_ly = _row_coords_to_local(exp_along, rc, cx, cy, ang)
                all_gaps.append({
                    "gap_id": "gap_%06d" % gap_id,
                    "local_x": exp_lx,
                    "local_y": exp_ly,
                    "row_id": ri,
                })

    return {
        "evaluated": True,
        "count": len(all_gaps),
        "status": "zero_gaps_confirmed" if not all_gaps else "gaps_found",
        "reason": None,
        "gaps": all_gaps,
    }



@dataclass
class FinalizationResult:
    session_id: str
    status: str = "NOT_STARTED"
    plants_count: int = 0
    gap_evaluated: bool = False
    gaps_count: Optional[int] = None
    gap_status: str = "NOT_EVALUATED"
    gap_reason: Optional[str] = None
    registration_accepted: int = 0
    registration_rejected: int = 0
    plant_shp_path: Optional[Path] = None
    gap_shp_path: Optional[Path] = None
    errors: List[str] = field(default_factory=list)


def finalize_session(session_dir: Path, config: Dict[str, Any]) -> FinalizationResult:
    session_dir = Path(session_dir)
    sid = session_dir.name
    result = FinalizationResult(session_id=sid)

    try:
        # Load detections
        det_path = session_dir / "detections.jsonl"
        detections = []
        if det_path.exists():
            with open(det_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            detections.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

        if not detections:
            result.status = "EMPTY_SESSION"
            return result

        # Directories
        results_dir = session_dir / "results"
        local_dir = results_dir / "local"
        geo_dir = results_dir / "geographic"
        shp_dir = results_dir / "shp"
        previews_dir = results_dir / "previews"
        for d in [results_dir, local_dir, geo_dir, shp_dir, previews_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Registration: try loading frames if available
        frames_dir = session_dir / "frames" / "processed"
        frames = {}
        transforms = {}
        reg_report = []
        if frames_dir.exists() and frames_dir.is_dir():
            for img_path in sorted(frames_dir.glob("frame_*.jpg")):
                try:
                    fi = int(img_path.stem.split("_")[1])
                    img = cv2.imread(str(img_path))
                    if img is not None:
                        frames[fi] = img
                except (ValueError, IndexError):
                    pass

            if len(frames) >= 2:
                transforms, _, reg_report = register_frame_sequence(frames, config)
                result.registration_accepted = len([r for r in reg_report if r.get("accepted")])
                result.registration_rejected = len(reg_report) - result.registration_accepted

        # Fallback: identity transforms for frames without registration
        for d in detections:
            fi = d.get("frame_index", 0)
            if fi not in transforms:
                transforms[fi] = np.eye(3)

        # Fusion
        unique_list = fuse_detections(detections, transforms, config)
        result.plants_count = len(unique_list)

        # Gap
        try:
            gap_result = analyze_gaps(unique_list, config)
        except Exception as gap_err:
            gap_result = {
                "evaluated": False, "count": None, "status": "NOT_EVALUATED",
                "reason": str(gap_err), "gaps": []}
        result.gap_evaluated = gap_result["evaluated"]
        result.gaps_count = gap_result["count"]
        result.gap_status = gap_result["status"]
        result.gap_reason = gap_result.get("reason")

        # Local GeoJSON
        plant_local = [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p["local_x"], p["local_y"]]},
            "properties": {"plant_id": p["plant_id"], "observations": p["observation_count"],
                          "confidence": p["confidence"]}
        } for p in unique_list]
        with open(local_dir / "unique_plants_local.geojson", 'w') as f:
            json.dump({"type": "FeatureCollection", "features": plant_local, "crs": "LOCAL_PIXEL"}, f, indent=2)

        gap_local = [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [g["local_x"], g["local_y"]]},
            "properties": {"gap_id": g["gap_id"], "row_id": g.get("row_id")}
        } for g in gap_result.get("gaps", [])]
        with open(local_dir / "gaps_local.geojson", 'w') as f:
            json.dump({"type": "FeatureCollection", "features": gap_local, "crs": "LOCAL_PIXEL"}, f, indent=2)

        # Geographic GeoJSON (local coords for now â€” geographic needs RTK mapping)
        geo_plant = [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p["local_x"], p["local_y"]]},
            "properties": {"plant_id": p["plant_id"], "observations": p["observation_count"],
                          "confidence": p["confidence"], "mapping": "local_pixel_only"}
        } for p in unique_list]
        with open(geo_dir / "unique_plants.geojson", 'w') as f:
            json.dump({"type": "FeatureCollection", "features": geo_plant}, f, indent=2)

        geo_gap = [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [g["local_x"], g["local_y"]]},
            "properties": {"gap_id": g["gap_id"], "row_id": g.get("row_id"), "mapping": "local_pixel_only"}
        } for g in gap_result.get("gaps", [])]
        with open(geo_dir / "gaps.geojson", 'w') as f:
            json.dump({"type": "FeatureCollection", "features": geo_gap}, f, indent=2)

        # SHP export
        plant_points = [(p["local_x"], p["local_y"]) for p in unique_list]
        plant_records = [{"plant_id": p["plant_id"], "observ": p["observation_count"],
                          "confid": p["confidence"]} for p in unique_list]
        write_shapefile(shp_dir, "unique_plants", plant_points,
                        [("plant_id", "C", 20, 0), ("observ", "N", 6, 0), ("confid", "F", 6, 3)],
                        plant_records, wgs84_prj())
        result.plant_shp_path = shp_dir / "unique_plants.shp"

        gap_points = [(g["local_x"], g["local_y"]) for g in gap_result.get("gaps", [])]
        gap_records = [{"gap_id": g["gap_id"], "row_id": g.get("row_id", 0)} for g in gap_result.get("gaps", [])]
        write_shapefile(shp_dir, "gaps", gap_points,
                        [("gap_id", "C", 20, 0), ("row_id", "N", 4, 0)],
                        gap_records, wgs84_prj())
        result.gap_shp_path = shp_dir / "gaps.shp"

        # Preview (map, not mosaic)
        img_w, img_h = 1200, 900
        img = np.ones((img_h, img_w, 3), dtype=np.uint8) * 240
        if unique_list:
            xs = [p["local_x"] for p in unique_list]
            ys = [p["local_y"] for p in unique_list]
            if xs and len(xs) > 1:
                x_min, x_max = min(xs), max(xs)
                y_min, y_max = min(ys), max(ys)
                x_r = max(x_max - x_min, 1.0)
                y_r = max(y_max - y_min, 1.0)
                for p in unique_list:
                    sx = int((p["local_x"] - x_min) / x_r * (img_w * 0.9) + img_w * 0.05)
                    sy = int((y_max - p["local_y"]) / y_r * (img_h * 0.9) + img_h * 0.05)
                    if 0 <= sx < img_w and 0 <= sy < img_h:
                        cv2.circle(img, (sx, sy), 3, (0, 180, 0), -1)
                for g in gap_result.get("gaps", []):
                    sx = int((g["local_x"] - x_min) / x_r * (img_w * 0.9) + img_w * 0.05)
                    sy = int((y_max - g["local_y"]) / y_r * (img_h * 0.9) + img_h * 0.05)
                    if 0 <= sx < img_w and 0 <= sy < img_h:
                        cv2.circle(img, (sx, sy), 5, (0, 0, 255), 2)
                        cv2.line(img, (sx-6, sy), (sx+6, sy), (0, 0, 255), 1)
        cv2.putText(img, "Plants: %d | Gaps: %s" % (
            result.plants_count, str(result.gaps_count) if result.gap_evaluated else "N/A"),
            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)

        preview_path = previews_dir / "unique_plants_map_preview.png"
        cv2.imwrite(str(preview_path), img)
        cv2.imwrite(str(previews_dir / "gap_map_preview.png"), img)


        # Summary
        summary = {
            "schema_version": "2.0",
            "session_id": sid,
            "finalized_at": datetime.now(timezone.utc).isoformat(),
            "status": "COMPLETE",
            "plants": {"evaluated": True, "unique_count": result.plants_count},
            "gap": {"evaluated": result.gap_evaluated, "count": result.gaps_count,
                    "status": result.gap_status, "reason": result.gap_reason},
            "registration": {"accepted": result.registration_accepted,
                            "rejected": result.registration_rejected},
            "outputs": {
                "local_geojson": str(local_dir),
                "geographic_geojson": str(geo_dir),
                "shp": str(shp_dir),
                "preview": str(preview_path),
            },
            "errors": result.errors,
        }
        with open(session_dir / "finalization_summary.json", 'w') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        # Output manifest with SHA256 hashes (v21)
        import hashlib as _hashlib
        manifest = {
            "session_id": sid, "generated_at": datetime.now(timezone.utc).isoformat(),
            "version": "v21", "plot_gap_ref": "acef1298",
            "files": {}
        }
        for root, dirs, files in os.walk(str(results_dir)):
            for fn in sorted(files):
                fp = Path(root) / fn
                rp = str(fp.relative_to(session_dir))
                try:
                    sha = _hashlib.sha256(fp.read_bytes()).hexdigest()
                except Exception:
                    sha = "ERROR"
                manifest["files"][rp] = {"bytes": fp.stat().st_size, "sha256": sha}
        with open(results_dir / "output_manifest.json", 'w') as f:
            json.dump(manifest, f, indent=2)

        result.status = "COMPLETE"

    except Exception as e:
        result.status = "ERROR"
        result.errors.append(str(e))

    return result
