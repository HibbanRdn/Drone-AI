from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Dict, Optional, Union

import cv2
import numpy as np

from .geometry import mask_to_contours
from .schema import ModelOutput


def sha256_file(path: Union[str, Path], chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_backend_path(model_config: Dict[str, Any], backend: str) -> Path:
    if backend not in {"auto", "pt", "onnx", "engine"}:
        raise ValueError(f"Backend tidak didukung: {backend}")
    candidates = (
        ("engine", "engine_path"),
        ("onnx", "onnx_path"),
        ("pt", "path"),
    )
    if backend != "auto":
        key = {"pt": "path", "onnx": "onnx_path", "engine": "engine_path"}[backend]
        path = Path(str(model_config[key])).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Model backend {backend} tidak ditemukan: {path}")
        return path
    for _, key in candidates:
        path = Path(str(model_config.get(key, ""))).expanduser().resolve()
        if path.is_file():
            return path
    raise FileNotFoundError(f"Tidak ada artefak model untuk {model_config.get('task')}")


class UltralyticsModel:
    def __init__(
        self,
        model_config: Dict[str, Any],
        *,
        backend: str,
        device: Union[str, int],
    ):
        from ultralytics import YOLO
        import ultralytics

        self.config = model_config
        self.expected_task = str(model_config["task"])
        self.path = resolve_backend_path(model_config, backend)
        self.backend = self.path.suffix.lower().lstrip(".") or backend
        self.device = device
        self.model = YOLO(str(self.path), task=self.expected_task)
        actual_task = str(getattr(self.model, "task", self.expected_task))
        if actual_task != self.expected_task:
            raise ValueError(
                f"Task model {self.path.name} adalah {actual_task}, expected {self.expected_task}"
            )
        configured_names = {
            int(key): str(value) for key, value in model_config.get("class_names", {}).items()
        }
        # On exported ONNX/TensorRT models, Ultralytics' public ``names``
        # property creates a temporary predictor/backend when no predictor is
        # initialized. Reading it here would deserialize the engine once, then
        # ``predict()`` would create a second execution context. Use embedded
        # PyTorch names when directly available; otherwise defer validation
        # until the persistent predictor has been initialized by warm-up.
        embedded_names = getattr(getattr(self.model, "model", None), "names", None)
        self._configured_names = configured_names
        self._names_validated = False
        self.names = configured_names.copy()
        if embedded_names is not None:
            self._validate_names(embedded_names)
        elif not configured_names:
            raise ValueError(
                "class_names wajib dikonfigurasi untuk backend export agar metadata "
                "tidak memicu backend sementara"
            )
        self._predictor_identity: Optional[int] = None
        self._backend_initialization_count = 0
        self.sha256 = sha256_file(self.path)
        self.ultralytics_version = ultralytics.__version__

    def _validate_names(self, names: Any) -> None:
        actual = {int(key): str(value) for key, value in names.items()}
        if self._configured_names and actual != self._configured_names:
            raise ValueError(
                f"Class mapping model berubah: checkpoint={actual}, "
                f"config={self._configured_names}"
            )
        self.names = actual
        self._names_validated = True

    def audit(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "backend": self.backend,
            "task": self.expected_task,
            "class_names": self.names,
            "sha256": self.sha256,
            "ultralytics_version": self.ultralytics_version,
            "image_size": int(self.config["image_size"]),
            "confidence_threshold": float(self.config["confidence_threshold"]),
            "nms_iou_threshold": float(self.config["nms_iou_threshold"]),
            "tiled": bool(self.config.get("tiled", False)),
            "tile_size": self.config.get("tile_size"),
            "tile_overlap": self.config.get("tile_overlap"),
            "global_nms_backend": self.config.get("global_nms_backend"),
            "center_suppression_enabled": bool(
                self.config.get("enable_center_suppression", False)
            ),
            "backend_initialization_count": self._backend_initialization_count,
            "names_validated": self._names_validated,
        }

    def warmup(self) -> ModelOutput:
        image_size = int(self.config["image_size"])
        frame = np.zeros((image_size, image_size, 3), dtype=np.uint8)
        output = self.predict(frame)
        if output.warning:
            raise RuntimeError(f"Model warm-up gagal: {output.warning}")
        return output

    def shutdown(self) -> None:
        self.model = None

    def _predict(self, image_bgr: np.ndarray) -> Any:
        results = self.model.predict(
            source=image_bgr,
            imgsz=int(self.config["image_size"]),
            conf=float(self.config["confidence_threshold"]),
            iou=float(self.config["nms_iou_threshold"]),
            max_det=int(self.config["max_detections"]),
            device=self.device,
            agnostic_nms=False,
            rect=False,
            retina_masks=bool(self.config.get("retina_masks", False)),
            verbose=False,
            save=False,
        )
        predictor = getattr(self.model, "predictor", None)
        predictor_identity = id(predictor) if predictor is not None else None
        if predictor_identity is not None and predictor_identity != self._predictor_identity:
            self._backend_initialization_count += 1
            self._predictor_identity = predictor_identity
        return results

    def predict(self, image_bgr: np.ndarray) -> ModelOutput:
        if image_bgr.ndim != 3 or image_bgr.shape[2] != 3 or image_bgr.size == 0:
            return ModelOutput(warning="empty_or_malformed_frame")
        started = time.perf_counter()
        try:
            results = self._predict(image_bgr)
            if not results:
                return ModelOutput(
                    latency_ms=(time.perf_counter() - started) * 1000,
                    warning="empty_model_output",
                )
            result = results[0]
            if not self._names_validated:
                result_names = getattr(result, "names", None)
                if result_names is None:
                    predictor = getattr(self.model, "predictor", None)
                    result_names = getattr(getattr(predictor, "model", None), "names", None)
                if result_names is None:
                    raise ValueError("Class mapping backend tidak tersedia setelah warm-up")
                self._validate_names(result_names)
            speed = getattr(result, "speed", {}) or {}
            if self.expected_task == "detect":
                output = self._parse_detector(result)
            else:
                output = self._parse_segmenter(result, image_bgr.shape[1], image_bgr.shape[0])
            output.latency_ms = (time.perf_counter() - started) * 1000
            output.preprocessing_ms = _float_or_none(speed.get("preprocess"))
            output.inference_ms = _float_or_none(speed.get("inference"))
            output.postprocessing_ms = _float_or_none(speed.get("postprocess"))
            return output
        except Exception as exc:
            return ModelOutput(
                latency_ms=(time.perf_counter() - started) * 1000,
                warning=f"{type(exc).__name__}: {exc}",
            )

    def _parse_detector(self, result: Any) -> ModelOutput:
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return ModelOutput()
        xyxy = boxes.xyxy.detach().cpu().numpy()
        confidence = boxes.conf.detach().cpu().numpy()
        classes = boxes.cls.detach().cpu().numpy().astype(int)
        detections = []
        for box, score, class_id in zip(xyxy, confidence, classes):
            if not np.all(np.isfinite(box)) or not np.isfinite(score):
                continue
            detections.append(
                {
                    "bbox_xyxy": [float(value) for value in box],
                    "confidence": float(score),
                    "class_id": int(class_id),
                    "class_name": self.names.get(int(class_id), str(class_id)),
                }
            )
        return ModelOutput(detections=detections)

    def _parse_segmenter(self, result: Any, width: int, height: int) -> ModelOutput:
        masks = getattr(result, "masks", None)
        if masks is None or getattr(masks, "data", None) is None or len(masks.data) == 0:
            return ModelOutput(mask=np.zeros((height, width), dtype=np.uint8))
        data = masks.data.detach().cpu().numpy()
        union = np.max(data, axis=0)
        if union.shape != (height, width):
            union = cv2.resize(union, (width, height), interpolation=cv2.INTER_LINEAR)
        binary = np.ascontiguousarray(union >= 0.5, dtype=np.uint8)
        open_kernel = int(self.config.get("morphology_open_kernel", 0))
        close_kernel = int(self.config.get("morphology_close_kernel", 0))
        if open_kernel > 1:
            kernel = np.ones((open_kernel, open_kernel), dtype=np.uint8)
            binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        if close_kernel > 1:
            kernel = np.ones((close_kernel, close_kernel), dtype=np.uint8)
            binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        contours = mask_to_contours(
            binary,
            min_area_px=float(self.config["contour_min_area_px"]),
            epsilon_ratio=float(self.config["contour_epsilon_ratio"]),
            max_points=int(self.config["contour_max_points"]),
        )
        return ModelOutput(contours=contours, mask=binary)


def select_device(requested: str) -> Union[str, int]:
    if requested not in {"auto", "cpu", "mps", "cuda", "0"}:
        raise ValueError("runtime.device harus auto/cpu/mps/cuda/0")
    if requested != "auto":
        return 0 if requested in {"cuda", "0"} else requested
    import torch

    if torch.cuda.is_available():
        return 0
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _float_or_none(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None
