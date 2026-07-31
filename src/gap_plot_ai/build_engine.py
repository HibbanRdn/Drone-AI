from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import onnx

from .config import load_config
from .models import sha256_file


def _onnx_metadata(path: Path) -> dict[str, Any]:
    graph = onnx.load(str(path), load_external_data=False)
    return {item.key: item.value for item in graph.metadata_props}


def _write_raw_engine(engine_path: Path) -> Path:
    with engine_path.open("rb") as source:
        metadata_length_raw = source.read(4)
        if len(metadata_length_raw) != 4:
            raise ValueError(f"Engine tidak memuat metadata Ultralytics: {engine_path}")
        metadata_length = int.from_bytes(metadata_length_raw, "little", signed=True)
        if not 0 < metadata_length < 1024 * 1024:
            raise ValueError(f"Panjang metadata engine tidak valid: {metadata_length}")
        metadata = json.loads(source.read(metadata_length).decode("utf-8"))
        engine_bytes = source.read()
    if not isinstance(metadata, dict) or not engine_bytes:
        raise ValueError("Engine metadata/payload tidak valid")
    raw_path = engine_path.with_suffix(".raw.engine")
    temporary = raw_path.with_suffix(".raw.engine.tmp")
    temporary.write_bytes(engine_bytes)
    os.replace(temporary, raw_path)
    return raw_path


def build_one(name: str, config: dict[str, Any], workspace_gib: int) -> dict[str, Any]:
    from ultralytics.utils.export.engine import onnx2engine
    import tensorrt
    import ultralytics

    onnx_path = Path(config["onnx_path"]).expanduser().resolve()
    engine_path = Path(config["engine_path"]).expanduser().resolve()
    if not onnx_path.is_file():
        raise FileNotFoundError(onnx_path)
    metadata = _onnx_metadata(onnx_path)
    required = {"task", "imgsz", "names", "args"}
    missing = sorted(required - metadata.keys())
    if missing:
        raise ValueError(f"Metadata ONNX {name} tidak lengkap: {missing}")
    engine_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = engine_path.with_suffix(".engine.tmp")
    onnx2engine(
        str(onnx_path),
        str(temporary),
        workspace=workspace_gib,
        quantize=16,
        dynamic=False,
        shape=(1, 3, int(config["image_size"]), int(config["image_size"])),
        metadata=metadata,
        verbose=False,
        prefix=f"{name}: ",
    )
    os.replace(temporary, engine_path)
    raw_path = _write_raw_engine(engine_path)
    return {
        "name": name,
        "onnx": str(onnx_path),
        "onnx_sha256": sha256_file(onnx_path),
        "engine": str(engine_path),
        "engine_sha256": sha256_file(engine_path),
        "engine_size_bytes": engine_path.stat().st_size,
        "raw_engine_for_trtexec": str(raw_path),
        "raw_engine_sha256": sha256_file(raw_path),
        "precision": "FP16",
        "dynamic": False,
        "workspace_gib": workspace_gib,
        "tensorrt_version": tensorrt.__version__,
        "ultralytics_version": ultralytics.__version__,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build TensorRT FP16 engines on Manifold 3")
    parser.add_argument("--config", required=True)
    parser.add_argument("--workspace-gib", type=int, default=2)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.workspace_gib < 1:
        raise ValueError("workspace-gib minimal 1")
    if os.uname().machine != "aarch64":
        raise RuntimeError("TensorRT engine hanya boleh dibangun pada Manifold 3 aarch64")
    config = load_config(args.config)
    reports = Path(config["runtime"]["root"]) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    report_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "models": [
            build_one(
                "plant_detector_b0_manual_v1_best",
                config["models"]["detector"],
                args.workspace_gib,
            ),
            build_one(
                "plot_segmenter_b4_selected_best",
                config["models"]["segmenter"],
                args.workspace_gib,
            ),
        ],
    }
    report = reports / "tensorrt_build_report.json"
    report.write_text(json.dumps(report_data, indent=2) + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
