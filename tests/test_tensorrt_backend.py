from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from gap_plot_ai.geometry import letterbox
from gap_plot_ai.tensorrt_backend import (
    DirectTensorRTModel,
    _opencv_nms,
    _resolve_engine_path,
    normalize_binding_metadata,
    validate_binding_contract,
)


def test_binding_metadata_requires_one_input_and_an_output() -> None:
    bindings = normalize_binding_metadata(
        (
            {
                "index": 0,
                "name": "images",
                "is_input": True,
                "dtype": "DataType.FLOAT",
                "shape": [1, 3, 1024, 1024],
            },
            {
                "index": 1,
                "name": "output0",
                "is_input": False,
                "dtype": "DataType.FLOAT",
                "shape": [1, 5, 21504],
            },
        )
    )
    validate_binding_contract(
        bindings,
        {"images": [1, 3, 1024, 1024], "output0": [1, 5, 21504]},
    )
    assert [binding.name for binding in bindings] == ["images", "output0"]


def test_binding_contract_rejects_wrong_engine_shape() -> None:
    bindings = normalize_binding_metadata(
        (
            {
                "name": "images",
                "is_input": True,
                "dtype": "float32",
                "shape": [1, 3, 640, 640],
            },
            {
                "name": "output0",
                "is_input": False,
                "dtype": "float32",
                "shape": [1, 5, 8400],
            },
        )
    )
    with pytest.raises(ValueError, match="shape differs"):
        validate_binding_contract(
            bindings,
            {"images": [1, 3, 1024, 1024], "output0": [1, 5, 21504]},
        )


def test_opencv_nms_is_class_aware_and_confidence_sorted() -> None:
    boxes = np.asarray(
        [[0, 0, 10, 10], [1, 1, 11, 11], [1, 1, 11, 11]], dtype=np.float32
    )
    scores = np.asarray([0.9, 0.8, 0.7], dtype=np.float32)
    classes = np.asarray([0, 0, 1], dtype=np.int32)
    kept = _opencv_nms(boxes, scores, classes, 0.1, 10)
    assert kept.tolist() == [0, 2]


def test_ultralytics_v8_decoder_restores_native_coordinates() -> None:
    model = DirectTensorRTModel.__new__(DirectTensorRTModel)
    model.names = {0: "plant"}
    model.config = {
        "confidence_threshold": 0.25,
        "nms_iou_threshold": 0.45,
        "max_detections": 20,
    }
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    _, meta = letterbox(image, (100, 200))
    output = np.asarray(
        [
            [
                [50.0, 51.0],
                [40.0, 41.0],
                [20.0, 20.0],
                [10.0, 10.0],
                [0.9, 0.8],
            ]
        ],
        dtype=np.float32,
    )
    detections = model._decode_detector(output, meta)
    assert len(detections) == 1
    np.testing.assert_allclose(detections[0]["bbox_xyxy"], [40, 35, 60, 45])
    assert detections[0]["class_name"] == "plant"


def test_read_only_engine_validator_guards_protected_directory() -> None:
    root = Path(__file__).parents[1]
    script = (root / "scripts/validate_engine_readonly.sh").read_text(
        encoding="utf-8"
    )
    assert "/home/dji/gap_plot_ai_assets/models/engine" in script
    assert "before_hash" in script and "after_hash" in script
    assert "--saveEngine" not in script


def test_direct_tensorrt_selects_single_engine_from_protected_directory(
    tmp_path: Path,
) -> None:
    engine = tmp_path / "plant_center_detector.engine"
    engine.write_bytes(b"readonly-engine-fixture")
    assert _resolve_engine_path({"engine_dir": str(tmp_path)}) == engine.resolve()


def test_direct_tensorrt_rejects_ambiguous_engine_directory(tmp_path: Path) -> None:
    (tmp_path / "a.engine").write_bytes(b"a")
    (tmp_path / "b.engine").write_bytes(b"b")
    with pytest.raises(FileNotFoundError, match="Expected exactly one"):
        _resolve_engine_path({"engine_dir": str(tmp_path)})
