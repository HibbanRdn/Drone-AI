from __future__ import annotations

import numpy as np

from gap_plot_ai.geometry import (
    clip_bbox,
    letterbox,
    mask_to_contours,
    preprocess_rgb_chw,
    rasterize_contours,
    reverse_letterbox_bbox,
)


def test_detector_preprocessing_shape_and_range() -> None:
    image = np.full((360, 640, 3), 255, dtype=np.uint8)
    tensor, meta = preprocess_rgb_chw(image, 1024)
    assert tensor.shape == (1, 3, 1024, 1024)
    assert tensor.dtype == np.float32
    assert 0 <= float(tensor.min()) <= float(tensor.max()) <= 1
    assert meta.source_width == 640


def test_segmenter_preprocessing_shape() -> None:
    image = np.zeros((512, 1024, 3), dtype=np.uint8)
    tensor, meta = preprocess_rgb_chw(image, 1280)
    assert tensor.shape == (1, 3, 1280, 1280)
    assert meta.scale == 1.25
    assert meta.pad_top == 320


def test_letterbox_coordinate_reversal_round_trip() -> None:
    image = np.zeros((500, 1000, 3), dtype=np.uint8)
    _, meta = letterbox(image, 640)
    source_box = (100.0, 50.0, 900.0, 450.0)
    boxed = (
        source_box[0] * meta.scale + meta.pad_left,
        source_box[1] * meta.scale + meta.pad_top,
        source_box[2] * meta.scale + meta.pad_left,
        source_box[3] * meta.scale + meta.pad_top,
    )
    restored = reverse_letterbox_bbox(boxed, meta)
    assert restored is not None
    np.testing.assert_allclose(restored, source_box, atol=1e-5)


def test_bbox_clipping_rejects_degenerate_and_nan() -> None:
    assert clip_bbox((-10, -5, 120, 60), 100, 50) == (0.0, 0.0, 100.0, 50.0)
    assert clip_bbox((10, 10, 5, 5), 100, 100) is None
    assert clip_bbox((0, 0, float("nan"), 5), 100, 100) is None


def test_mask_to_contour_simplifies_and_preserves_area() -> None:
    mask = np.zeros((200, 300), dtype=np.uint8)
    mask[20:180, 40:260] = 1
    contours = mask_to_contours(
        mask, min_area_px=100, epsilon_ratio=0.001, max_points=16
    )
    assert len(contours) == 1
    assert 3 <= len(contours[0]) <= 16
    restored = rasterize_contours(contours, 300, 200)
    intersection = np.logical_and(mask, restored).sum()
    union = np.logical_or(mask, restored).sum()
    assert intersection / union > 0.98


def test_mask_to_contour_handles_empty_or_malformed() -> None:
    assert mask_to_contours(
        np.zeros((0, 0), dtype=np.uint8),
        min_area_px=1,
        epsilon_ratio=0.01,
        max_points=10,
    ) == []
    assert mask_to_contours(
        np.zeros((10, 10, 3), dtype=np.uint8),
        min_area_px=1,
        epsilon_ratio=0.01,
        max_points=10,
    ) == []
