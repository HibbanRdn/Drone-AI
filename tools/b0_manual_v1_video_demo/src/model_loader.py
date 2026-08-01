from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
import ultralytics
from ultralytics import YOLO


LOGGER = logging.getLogger(__name__)


@dataclass
class DeviceDecision:
    requested_device: str
    actual_device: str
    mps_built: bool
    mps_available: bool
    mps_smoke_succeeded: bool | None
    fallback_used: bool
    fallback_reason: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class PlantCenterModel:
    def __init__(
        self,
        model_path: str | Path,
        requested_device: str = "auto",
        allow_mps_to_cpu_fallback: bool = True,
    ):
        path = Path(model_path).expanduser().resolve()
        if path.name not in {"best.pt", "plant_center_manual_v1_b0_best.pt"} or not path.is_file():
            raise ValueError(f"Checkpoint wajib berupa B0 manual-v1 resmi yang valid: {path}")
        if requested_device not in {"auto", "mps", "cpu"}:
            raise ValueError("Hanya device auto, mps, dan cpu yang didukung.")
        self.path = path
        self.requested_device = requested_device
        self.allow_fallback = bool(allow_mps_to_cpu_fallback)
        self.model = YOLO(str(path), task="detect")
        if getattr(self.model, "task", None) != "detect":
            raise ValueError(f"Task checkpoint bukan detection: {self.model.task}")
        self.names = {int(key): str(value) for key, value in self.model.names.items()}
        self.device = "cpu"
        self.device_decision: DeviceDecision | None = None
        self.runtime_fallback_events: list[str] = []

    def select_device(self, smoke_image: np.ndarray, config: dict[str, Any]) -> DeviceDecision:
        mps_built = bool(torch.backends.mps.is_built())
        mps_available = bool(torch.backends.mps.is_available())
        wants_mps = self.requested_device in {"auto", "mps"}
        smoke_succeeded: bool | None = None
        fallback_used = False
        fallback_reason: str | None = None

        if wants_mps and mps_available:
            self.device = "mps"
            try:
                self._predict(smoke_image, config)
                smoke_succeeded = True
            except Exception as exc:  # MPS failures vary across torch/Ultralytics releases.
                smoke_succeeded = False
                fallback_reason = f"{type(exc).__name__}: {exc}"
                if not self.allow_fallback:
                    raise RuntimeError(f"Smoke inference MPS gagal: {fallback_reason}") from exc
                fallback_used = True
                self.device = "cpu"
                LOGGER.warning("MPS gagal; fallback CPU: %s", fallback_reason)
                self._predict(smoke_image, config)
        elif wants_mps and not mps_available:
            fallback_reason = "torch.backends.mps.is_available() returned False"
            if self.requested_device == "mps" and not self.allow_fallback:
                raise RuntimeError(fallback_reason)
            fallback_used = self.requested_device == "mps" or self.requested_device == "auto"
            self.device = "cpu"
            self._predict(smoke_image, config)
        else:
            self.device = "cpu"
            self._predict(smoke_image, config)

        self.device_decision = DeviceDecision(
            requested_device=self.requested_device,
            actual_device=self.device,
            mps_built=mps_built,
            mps_available=mps_available,
            mps_smoke_succeeded=smoke_succeeded,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
        )
        return self.device_decision

    def _predict(self, image: np.ndarray, config: dict[str, Any]) -> list[dict[str, object]]:
        results = self.model.predict(
            source=image,
            imgsz=int(config["image_size"]),
            conf=float(config["confidence_threshold"]),
            iou=float(config["nms_iou_threshold"]),
            max_det=int(config["max_detections_per_tile"]),
            device=self.device,
            agnostic_nms=False,
            verbose=False,
            save=False,
        )
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return []
        xyxy = boxes.xyxy.detach().cpu().numpy()
        confidences = boxes.conf.detach().cpu().numpy()
        class_ids = boxes.cls.detach().cpu().numpy().astype(int)
        return [
            {
                "x1": float(box[0]),
                "y1": float(box[1]),
                "x2": float(box[2]),
                "y2": float(box[3]),
                "confidence": float(confidence),
                "class_id": int(class_id),
                "class_name": self.names.get(int(class_id), str(class_id)),
            }
            for box, confidence, class_id in zip(xyxy, confidences, class_ids, strict=True)
        ]

    def predict(self, image: np.ndarray, config: dict[str, Any]) -> list[dict[str, object]]:
        try:
            return self._predict(image, config)
        except Exception as exc:
            if self.device != "mps" or not self.allow_fallback:
                raise
            reason = f"Runtime MPS fallback: {type(exc).__name__}: {exc}"
            LOGGER.warning(reason)
            self.runtime_fallback_events.append(reason)
            self.device = "cpu"
            if self.device_decision:
                self.device_decision.actual_device = "cpu"
                self.device_decision.fallback_used = True
                self.device_decision.fallback_reason = reason
            return self._predict(image, config)

    def audit(self) -> dict[str, object]:
        model_args = getattr(getattr(self.model, "model", None), "args", {}) or {}
        return {
            "path": str(self.path),
            "file_size_bytes": self.path.stat().st_size,
            "checkpoint_loaded": True,
            "task": self.model.task,
            "class_mapping": self.names,
            "class_names": list(self.names.values()),
            "ultralytics_runtime_version": ultralytics.__version__,
            "checkpoint_args": {
                key: model_args.get(key)
                for key in ("task", "imgsz", "max_det", "conf", "iou")
                if key in model_args
            },
        }
