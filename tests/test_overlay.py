from __future__ import annotations

from gap_plot_ai.overlay import (
    CoordinateMapper,
    PSDK_BOX_COUNT_LIMIT,
    select_overlay_detections,
    static_debug_boxes,
)


def _detection(confidence: float, x: float) -> dict:
    return {
        "bbox_xyxy": [x, 10.0, x + 5.0, 20.0],
        "confidence": confidence,
        "class_id": 0,
    }


def test_identity_mapping_covers_center_and_four_edges() -> None:
    mapper = CoordinateMapper(1920, 1080)
    boxes = static_debug_boxes(1920, 1080)
    mapped = [mapper.map_box(item["bbox_xyxy"]) for item in boxes]
    center = mapped[0]
    assert 4500 < center[0] < 5000 < center[2] < 5500
    assert mapped[1][0] < 1000 and mapped[1][1] < 1000
    assert mapped[2][2] > 9000 and mapped[2][1] < 1000
    assert mapped[3][0] < 1000 and mapped[3][3] > 9000
    assert mapped[4][2] > 9000 and mapped[4][3] > 9000


def test_rotation_and_letterbox_mapping_are_clamped() -> None:
    mapper = CoordinateMapper(
        1920, 1080, aspect_mode="contain", rotation_degrees=90
    )
    mapped = mapper.map_box([0, 0, 1920, 1080])
    assert all(0 <= value <= 10000 for value in mapped)
    assert mapped[2] > mapped[0] and mapped[3] > mapped[1]


def test_overlay_auto_limit_is_official_uint8_abi_and_transparent() -> None:
    detections = [_detection(index / 300.0, float(index)) for index in range(300)]
    selection = select_overlay_detections(
        detections,
        max_objects="auto",
        min_confidence=0.0,
        frame_width=1920,
        frame_height=1080,
    )
    assert selection.api_limit == PSDK_BOX_COUNT_LIMIT == 255
    assert selection.total_detections == 300
    assert selection.sent_objects == 255
    assert selection.truncated_objects == 45
    assert selection.detections[0]["confidence"] > selection.detections[-1]["confidence"]
