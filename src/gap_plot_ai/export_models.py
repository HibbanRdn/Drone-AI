from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_config
from .models import sha256_file


def export_one(name: str, model_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    from ultralytics import YOLO
    import onnx
    import torch
    import ultralytics

    source = Path(model_config["path"]).expanduser().resolve()
    source_hash = sha256_file(source)
    expected_hash = str(model_config["source_sha256"])
    if source_hash != expected_hash:
        raise ValueError(f"SHA-256 {name} tidak sama dengan registry/config")
    link = output_dir / f"{name}.pt"
    if link.is_symlink():
        if link.resolve() != source:
            raise ValueError(f"Symlink export menunjuk target lain: {link}")
    elif link.exists():
        raise FileExistsError(f"Tidak akan menimpa file export staging: {link}")
    else:
        link.symlink_to(source)
    model = YOLO(str(link), task=str(model_config["task"]))
    names = {int(key): str(value) for key, value in model.names.items()}
    expected_names = {
        int(key): str(value) for key, value in model_config["class_names"].items()
    }
    if model.task != model_config["task"] or names != expected_names:
        raise ValueError(
            f"Metadata checkpoint {name} berubah: task={model.task}, names={names}"
        )
    exported = Path(
        model.export(
            format="onnx",
            imgsz=int(model_config["image_size"]),
            batch=1,
            dynamic=False,
            simplify=False,
            opset=17,
            half=False,
            int8=False,
            nms=False,
            device="cpu",
        )
    ).resolve()
    target = Path(model_config["onnx_path"]).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if exported != target:
        if target.exists():
            target.unlink()
        os.replace(exported, target)
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
        "opset": 17,
        "dynamic": False,
        "simplify": False,
        "half": False,
        "nms_embedded": False,
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
            export_one("plant_detector_b0_manual_v1_best", config["models"]["detector"], output_dir),
            export_one("plot_segmenter_b4_selected_best", config["models"]["segmenter"], output_dir),
        ],
    }
    report = reports / "model_export_report.json"
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
