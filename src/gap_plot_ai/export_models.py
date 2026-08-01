from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .config import load_config
from .models import sha256_file


def export_one(
    name: str, model_config: Dict[str, Any], output_dir: Path
) -> Dict[str, Any]:
    from ultralytics import YOLO
    import onnx
    import torch
    import ultralytics

    source = Path(model_config["path"]).expanduser().resolve()
    source_hash = sha256_file(source)
    expected_hash = str(model_config["source_sha256"])
    if source_hash != expected_hash:
        raise ValueError(f"SHA-256 {name} tidak sama dengan registry/config")
    export_config = model_config["onnx_export"]
    model = YOLO(str(source), task=str(model_config["task"]))
    names = {int(key): str(value) for key, value in model.names.items()}
    expected_names = {
        int(key): str(value) for key, value in model_config["class_names"].items()
    }
    if model.task != model_config["task"] or names != expected_names:
        raise ValueError(
            f"Metadata checkpoint {name} berubah: task={model.task}, names={names}"
        )
    target = Path(model_config["onnx_path"]).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{name}_", dir=str(output_dir)) as temporary_dir:
        staged_source = Path(temporary_dir) / f"{name}.pt"
        staged_source.symlink_to(source)
        staged_model = YOLO(str(staged_source), task=str(model_config["task"]))
        exported = Path(
            staged_model.export(
                format="onnx",
                imgsz=int(model_config["image_size"]),
                batch=int(export_config["batch"]),
                dynamic=bool(export_config["dynamic"]),
                simplify=bool(export_config["simplify"]),
                opset=int(export_config["opset"]),
                half=bool(export_config["fp16"]),
                int8=bool(export_config["int8"]),
                nms=bool(export_config["embedded_nms"]),
                device="cpu",
            )
        ).resolve()
        temporary_target = target.with_suffix(target.suffix + ".tmp")
        shutil.copyfile(exported, temporary_target)
        os.replace(temporary_target, target)
    graph = onnx.load(str(target))
    onnx.checker.check_model(graph)
    return {
        "name": name,
        "source": str(source),
        "source_sha256": source_hash,
        "source_size_bytes": source.stat().st_size,
        "task": model.task,
        "class_names": names,
        "image_size": int(model_config["image_size"]),
        "opset": int(export_config["opset"]),
        "dynamic": bool(export_config["dynamic"]),
        "simplify": bool(export_config["simplify"]),
        "half": bool(export_config["fp16"]),
        "nms_embedded": bool(export_config["embedded_nms"]),
        "onnx": str(target),
        "onnx_sha256": sha256_file(target),
        "onnx_size_bytes": target.stat().st_size,
        "onnx_ir_version": int(graph.ir_version),
        "torch_version": torch.__version__,
        "ultralytics_version": ultralytics.__version__,
        "onnx_version": onnx.__version__,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export audited PT models to ONNX")
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-dir")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    output_dir = Path(
        args.output_dir or Path(config["runtime"]["root"]) / "models"
    ).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    reports = Path(config["runtime"]["root"]) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "models": [
            export_one("plant_center_manual_v1_b0_best", config["models"]["detector"], output_dir),
            export_one("plot_segmenter_b4_selected_best", config["models"]["segmenter"], output_dir),
        ],
    }
    report = reports / "model_export_report.json"
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
