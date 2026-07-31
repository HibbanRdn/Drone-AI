from __future__ import annotations

from gap_plot_ai.tiling import (
    TileWindow,
    TiledDetection,
    center_distance_suppression,
    class_aware_nms,
    generate_tile_windows,
    local_box_to_global,
    merge_detections,
)


def _detection(
    box: tuple[float, float, float, float],
    confidence: float,
    class_id: int = 0,
) -> TiledDetection:
    return TiledDetection(
        class_id=class_id,
        class_name="plant",
        confidence=confidence,
        x1=box[0],
        y1=box[1],
        x2=box[2],
        y2=box[3],
        source_tile_x=0,
        source_tile_y=0,
        source_tile_width=1024,
        source_tile_height=1024,
    )


def test_generate_windows_matches_demo_overlap_and_covers_edges() -> None:
    windows = generate_tile_windows(3840, 2160, 1024, 128)
    assert len(windows) == 15
    assert sorted({window.x for window in windows}) == [0, 896, 1792, 2688, 2816]
    assert sorted({window.y for window in windows}) == [0, 896, 1136]
    assert windows[-1] == TileWindow(2816, 1136, 1024, 1024)


def test_generate_single_window_for_small_frame() -> None:
    assert generate_tile_windows(32, 24, 1024, 128) == [
        TileWindow(0, 0, 32, 24)
    ]


def test_local_box_to_global_coordinates() -> None:
    assert local_box_to_global(
        [1.0, 2.0, 11.0, 12.0], TileWindow(896, 128, 1024, 1024)
    ) == (897.0, 130.0, 907.0, 140.0)


def test_class_aware_nms_keeps_highest_and_different_class() -> None:
    detections = [
        _detection((0, 0, 10, 10), 0.9),
        _detection((1, 1, 11, 11), 0.8),
        _detection((1, 1, 11, 11), 0.7, class_id=1),
    ]
    kept = class_aware_nms(detections, 0.1)
    assert [(item.class_id, item.confidence) for item in kept] == [(0, 0.9), (1, 0.7)]


def test_center_suppression_removes_close_center_duplicate() -> None:
    detections = [
        _detection((0, 0, 10, 10), 0.9),
        _detection((2, 2, 12, 12), 0.8),
        _detection((30, 30, 40, 40), 0.7),
    ]
    kept = center_distance_suppression(detections, 8.0)
    assert [item.confidence for item in kept] == [0.9, 0.7]


def test_merge_diagnostics_and_full_frame_cap() -> None:
    detections = [
        _detection((0, 0, 10, 10), 0.9),
        _detection((1, 1, 11, 11), 0.8),
        _detection((30, 30, 40, 40), 0.7),
    ]
    kept, diagnostics = merge_detections(
        detections,
        global_nms_iou=0.1,
        center_duplicate_radius_px=8.0,
        enable_center_suppression=True,
        max_detections_full_frame=1,
    )
    assert len(kept) == 1
    assert diagnostics == {
        "raw_tile_predictions": 3,
        "after_global_nms": 2,
        "after_center_suppression": 1,
        "duplicates_removed": 2,
    }
