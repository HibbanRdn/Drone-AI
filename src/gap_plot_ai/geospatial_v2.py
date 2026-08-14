"""vNext: RTK geospatial utilities. PSDK 3.16.0 position solution mapper,
WGS84/UTM conversion, local-to-world transform fitting."""

import math
from typing import Any, Dict, List, Optional, Tuple
import numpy as np


# ============================================================
# RTK SEMANTIC MAPPER (from PSDK 3.16.0 dji_fc_subscription.h)
# ============================================================
RTK_SOLUTION_MAP = {
    0: "NOT_AVAILABLE",
    1: "FIX_POSITION",
    2: "FIX_HEIGHT_AUTO",
    8: "INSTANTANEOUS_DOPPLER_VELOCITY",
    16: "SINGLE_PNT_SOLUTION",
    17: "PSEUDORANGE_DIFFERENTIAL",
    18: "SBAS_CORRECTION",
    19: "KALMAN_FILTER_PROPAGATED",
    20: "OMNISTAR_VBS",
    32: "FLOAT_L1_AMBIGUITY",
    33: "FLOAT_IONOSPHERIC_FREE_AMBIGUITY",
    34: "FLOAT_SOLUTION",
    48: "L1_AMBIGUITY_INT",
    49: "WIDE_LANE_AMBIGUITY_INT",
    50: "NARROW_INT",
}


def classify_rtk(raw_status: Optional[int]) -> Dict[str, Any]:
    if raw_status is None or raw_status < 0:
        return {"raw": None, "semantic": "UNKNOWN", "quality": "UNAVAILABLE", "is_fixed": False}
    raw = int(raw_status)
    semantic = RTK_SOLUTION_MAP.get(raw, "UNKNOWN_%d" % raw)

    # Quality: only classify KNOWN PSDK values
    if raw in RTK_SOLUTION_MAP:
        if raw >= 48:
            quality = "FIXED"
        elif raw >= 32:
            quality = "FLOAT"
        elif raw >= 16:
            quality = "APPROXIMATE"
        elif raw >= 1:
            quality = "LOW_QUALITY"
        else:
            quality = "UNAVAILABLE"
    else:
        quality = "UNKNOWN"

    return {"raw": raw, "semantic": semantic, "quality": quality, "is_fixed": quality == "FIXED"}

def wgs84_to_utm_zone(lon_deg: float) -> int:
    return int(math.floor((lon_deg + 180.0) / 6.0)) + 1


def wgs84_to_utm(lat_deg: float, lon_deg: float) -> Tuple[float, float, int]:
    """Convert WGS84 to UTM easting/northing, zone."""
    zone = wgs84_to_utm_zone(lon_deg)
    a = 6378137.0
    f = 1.0 / 298.257223563
    k0 = 0.9996
    e = math.sqrt(2 * f - f * f)

    lat_rad = math.radians(lat_deg)
    lon_rad = math.radians(lon_deg)
    lon0_rad = math.radians((zone - 1) * 6.0 - 180.0 + 3.0)

    e2 = e * e
    n = a / math.sqrt(1 - e2 * math.sin(lat_rad)**2)
    t = math.tan(lat_rad)**2
    c = e2 / (1 - e2) * math.cos(lat_rad)**2
    A = (lon_rad - lon0_rad) * math.cos(lat_rad)

    M = a * ((1 - e2/4 - 3*e2*e2/64 - 5*e2*e2*e2/256) * lat_rad
             - (3*e2/8 + 3*e2*e2/32 + 45*e2*e2*e2/1024) * math.sin(2*lat_rad)
             + (15*e2*e2/256 + 45*e2*e2*e2/1024) * math.sin(4*lat_rad)
             - (35*e2*e2*e2/3072) * math.sin(6*lat_rad))

    easting = k0 * n * (A + (1 - t + c) * A**3/6 + (5 - 18*t + t**2 + 72*c - 58*e2/(1-e2)) * A**5/120) + 500000.0
    northing = k0 * (M + n * math.tan(lat_rad) * (A**2/2 + (5 - t + 9*c + 4*c**2) * A**4/24
                     + (61 - 58*t + t**2 + 600*c - 330*e2/(1-e2)) * A**6/720))
    if lat_deg < 0:
        northing += 10000000.0

    return easting, northing, zone


def utm_to_wgs84(easting: float, northing: float, zone: int, southern: bool = False) -> Tuple[float, float]:
    """Convert UTM back to WGS84 lat/lon."""
    a = 6378137.0
    f = 1.0 / 298.257223563
    k0 = 0.9996
    e = math.sqrt(2 * f - f * f)

    e1 = (1 - math.sqrt(1 - e*e)) / (1 + math.sqrt(1 - e*e))
    x = easting - 500000.0
    y = northing
    if southern:
        y -= 10000000.0

    M = y / k0
    mu = M / (a * (1 - e*e/4 - 3*e*e*e*e/64 - 5*e*e*e*e*e*e/256))

    phi1 = mu + (3*e1/2 - 27*e1*e1*e1/32) * math.sin(2*mu) \
           + (21*e1*e1/16 - 55*e1*e1*e1*e1/32) * math.sin(4*mu) \
           + (151*e1*e1*e1/96) * math.sin(6*mu)

    N1 = a / math.sqrt(1 - e*e * math.sin(phi1)**2)
    T1 = math.tan(phi1)**2
    C1 = e*e / (1 - e*e) * math.cos(phi1)**2
    R1 = a * (1 - e*e) / (1 - e*e * math.sin(phi1)**2)**1.5
    D = x / (N1 * k0)

    lat = phi1 - (N1 * math.tan(phi1) / R1) * (D*D/2 - (5 + 3*T1 + 10*C1 - 4*C1*C1 - 9*e*e/(1-e*e)) * D*D*D*D/24
          + (61 + 90*T1 + 298*C1 + 45*T1*T1 - 252*e*e/(1-e*e) - 3*C1*C1) * D*D*D*D*D*D/720)
    lon = (D - (1 + 2*T1 + C1) * D*D*D/6
           + (5 - 2*C1 + 28*T1 - 3*C1*C1 + 8*e*e/(1-e*e) + 24*T1*T1) * D*D*D*D*D/120) / math.cos(phi1)

    lat_deg = math.degrees(lat)
    lon_deg = math.degrees(lon) + (zone - 1) * 6.0 - 180.0 + 3.0

    return lat_deg, lon_deg


# ============================================================
# SIMILARITY TRANSFORM FIT (local → world)
# ============================================================
def fit_similarity(
    source: np.ndarray,  # Nx2 local points
    target: np.ndarray,  # Nx2 world points (metric: UTM/ENU)
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """Fit a 2D similarity transform (rotation + uniform scale + translation)
    from source (local visual map) to target (metric world coordinates).
    Returns (3x3 matrix or None, diagnostics)."""

    n = len(source)
    if n < 3:
        return None, {"status": "insufficient_correspondences", "count": n}

    src_mean = source.mean(axis=0)
    tgt_mean = target.mean(axis=0)
    src_centered = source - src_mean
    tgt_centered = target - tgt_mean

    # SVD for rotation
    H = src_centered.T @ tgt_centered
    U, _, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T

    # Ensure proper rotation (det = +1)
    if np.linalg.det(R) < 0:
        Vt[-1, :] *= -1
        R = Vt.T @ U.T

    # Scale
    src_var = np.sum(src_centered ** 2)
    if src_var < 1e-10:
        return None, {"status": "degenerate_source", "variance": src_var}
    scale = np.sum(tgt_centered * (src_centered @ R.T)) / src_var
    if scale <= 0 or not (0.001 < scale < 10000):
        return None, {"status": "invalid_scale", "scale": scale}

    # Translation
    translation = tgt_mean - scale * (src_mean @ R.T)

    # Residuals
    predicted = scale * (source @ R.T) + translation
    residuals = np.sqrt(np.sum((target - predicted) ** 2, axis=1))
    p95 = float(np.percentile(residuals, 95))
    median = float(np.median(residuals))
    max_r = float(np.max(residuals))

    # Build 3x3 matrix
    M = np.eye(3)
    M[:2, :2] = scale * R
    M[:2, 2] = translation

    # QA
    qa_passed = (p95 < 10.0 and median < 5.0 and scale > 0.001)

    return M, {
        "status": "accepted" if qa_passed else "rejected_high_residual",
        "correspondences": n, "p95_residual_m": p95, "median_residual_m": median,
        "max_residual_m": max_r, "scale": scale, "rotation_deg": math.degrees(math.atan2(R[1, 0], R[0, 0])),
    }
