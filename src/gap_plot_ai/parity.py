from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import cv2
import numpy as np

from .config import load_config
from .geometry import rasterize_contours
from .models import UltralyticsModel, select_device
from .tiling import predict_tiled_detector


def _iou(box_a: List[float], box_b: List[float]) -> float:
    left = max(box_a[0], box_b[0])
    top = max(box_a[1], box_b[1])
    right = min(box_a[2], box_b[2])
    bottom = min(box_a[3], box_b[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    area_a = max(0.0, box_a[2] - box_a[0]) * max(0.0, box_a[3] - box_a[1])
    area_b = max(0.0, box_b[2] - box_b[0]) * max(0.0, box_b[3] - box_b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def compare_detector(
    reference: Any, candidate: Any, tolerance: Dict[str, Any]
) -> Dict[str, Any]:
    reference_remaining = set(range(len(reference.detections)))
    candidate_remaining = set(range(len(candidate.detections)))
    matches = []
    pairs = []
    for reference_index, ref in enumerate(reference.detections):
        for candidate_index, cand in enumerate(candidate.detections):
            if ref["class_id"] != cand["class_id"]:
                continue
            pairs.append(
                (
                    _iou(ref["bbox_xyxy"], cand["bbox_xyxy"]),
                    reference_index,
                    candidate_index,
                )
            )
    for overlap, reference_index, candidate_index in sorted(pairs, reverse=True):
        if overlap < 0.5:
            break
        if (
            reference_index not in reference_remaining
            or candidate_index not in candidate_remaining
        ):
            continue
        reference_remaining.remove(reference_index)
        candidate_remaining.remove(candidate_index)
        ref = reference.detections[reference_index]
        cand = candidate.detections[candidate_index]
        matches.append(
            {
                "iou": overlap,
                "confidence_abs_error": abs(ref["confidence"] - cand["confidence"]),
                "bbox_max_abs_error_px": float(
                    np.max(
                        np.abs(
                            np.asarray(ref["bbox_xyxy"])
                            - np.asarray(cand["bbox_xyxy"])
                        )
                    )
                ),
            }
        )
    unmatched = len(reference_remaining) + len(candidate_remaining)
    count_delta = abs(len(reference.detections) - len(candidate.detections))
    max_conf = max((item["confidence_abs_error"] for item in matches), default=0.0)
    max_bbox = max((item["bbox_max_abs_error_px"] for item in matches), default=0.0)
    passed = (
        count_delta <= int(tolerance["detector_count_tolerance"])
        and unmatched <= int(tolerance["detector_count_tolerance"])
        and max_conf <= float(tolerance["detector_confidence_abs_tolerance"])
        and max_bbox <= float(tolerance["detector_bbox_abs_tolerance_px"])
    )
    return {
        "passed": passed,
        "reference_count": len(reference.detections),
        "candidate_count": len(candidate.detections),
        "count_delta": count_delta,
        "unmatched_total": unmatched,
        "matched": len(matches),
        "max_confidence_abs_error": max_conf,
        "max_bbox_abs_error_px": max_bbox,
        "mean_iou": float(np.mean([item["iou"] for item in matches])) if matches else None,
    }


def compare_segmenter(
    reference: Any, candidate: Any, width: int, height: int, minimum_iou: float
) -> Dict[str, Any]:
    reference_mask = (
        reference.mask
        if isinstance(reference.mask, np.ndarray)
        else rasterize_contours(reference.contours, width, height)
    )
    candidate_mask = (
        candidate.mask
        if isinstance(candidate.mask, np.ndarray)
        else rasterize_contours(candidate.contours, width, height)
    )
    intersection = int(np.logical_and(reference_mask, candidate_mask).sum())
    union = int(np.logical_or(reference_mask, candidate_mask).sum())
    iou = intersection / union if union else 1.0
    return {
        "passed": iou >= minimum_iou,
        "union_iou": iou,
        "minimum_iou": minimum_iou,
        "reference_foreground_px": int(reference_mask.sum()),
        "candidate_foreground_px": int(candidate_mask.sum()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PT versus ONNX/TensorRT parity")
    parser.add_argument("--config", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--candidate-backend", choices=("onnx", "engine"), required=True)
    parser.add_argument("--frames", type=int, nargs="+", default=[0, 30, 60])
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    video = Path(args.video).expanduser().resolve()
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise OSError(video)
    # ONNX export parity must isolate serialization/backend differences from
    # MPS/CoreML provider differences. TensorRT parity runs both sides on CUDA.
    device = (
        "cpu"
        if args.candidate_backend == "onnx"
        else select_device(str(config["runtime"]["device"]))
    )
    detector_pt = UltralyticsModel(config["models"]["detector"], backend="pt", device=device)
    detector_candidate = UltralyticsModel(
        config["models"]["detector"], backend=args.candidate_backend, device=device
    )
    segmenter_pt = UltralyticsModel(config["models"]["segmenter"], backend="pt", device=device)
    segmenter_candidate = UltralyticsModel(
        config["models"]["segmenter"], backend=args.candidate_backend, device=device
    )
    records = []
    try:
        for frame_index in args.frames:
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok:
                raise OSError(f"Frame {frame_index} tidak dapat dibaca")
            if bool(config["models"]["detector"].get("tiled", False)):
                det_ref = predict_tiled_detector(
                    detector_pt, frame, config["models"]["detector"]
                )
                det_candidate = predict_tiled_detector(
                    detector_candidate, frame, config["models"]["detector"]
                )
            else:
                det_ref = detector_pt.predict(frame)
                det_candidate = detector_candidate.predict(frame)
            seg_ref = segmenter_pt.predict(frame)
            seg_candidate = segmenter_candidate.predict(frame)
            if any(
                output.warning
                for output in (det_ref, det_candidate, seg_ref, seg_candidate)
            ):
                raise RuntimeError(
                    f"Model output warning pada frame {frame_index}: "
                    f"{[o.warning for o in (det_ref, det_candidate, seg_ref, seg_candidate)]}"
                )
            records.append(
                {
                    "frame_index": frame_index,
                    "detector": compare_detector(det_ref, det_candidate, config["parity"]),
                    "segmenter": compare_segmenter(
                        seg_ref,
                        seg_candidate,
                        frame.shape[1],
                        frame.shape[0],
                        float(config["parity"]["segmenter_union_iou_min"]),
                    ),
                    "latency_ms": {
                        "detector_pt": det_ref.latency_ms,
                        f"detector_{args.candidate_backend}": det_candidate.latency_ms,
                        "segmenter_pt": seg_ref.latency_ms,
                        f"segmenter_{args.candidate_backend}": seg_candidate.latency_ms,
                    },
                }
            )
    finally:
        capture.release()
    passed = all(
        record["detector"]["passed"] and record["segmenter"]["passed"]
        for record in records
    )
    report_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reference_backend": "pt",
        "candidate_backend": args.candidate_backend,
        "video": str(video),
        "passed": passed,
        "tolerance": config["parity"],
        "records": records,
    }
    reports = Path(config["runtime"]["root"]) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    report = reports / f"parity_pt_{args.candidate_backend}.json"
    report.write_text(json.dumps(report_data, indent=2) + "\n", encoding="utf-8")
    print(report)
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
