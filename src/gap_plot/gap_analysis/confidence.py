"""Confidence helpers."""

from __future__ import annotations


def confidence_level(score: float) -> str:
    """Convert a numeric confidence score into a coarse label."""

    if not 0 <= score <= 1:
        raise ValueError("Confidence score must be in the range [0, 1].")
    if score >= 0.80:
        return "high"
    if score >= 0.50:
        return "medium"
    return "low"
