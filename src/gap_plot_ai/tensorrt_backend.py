from __future__ import annotations

import ctypes
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np

from .geometry import preprocess_rgb_chw, reverse_letterbox_bbox
from .schema import ModelOutput


CUDA_MEMCPY_HOST_TO_DEVICE = 1
CUDA_MEMCPY_DEVICE_TO_HOST = 2


@dataclass(frozen=True)
class BindingMetadata:
    index: int
    name: str
    is_input: bool
    dtype: str
    shape: Tuple[int, ...]
    dynamic: bool


def normalize_binding_metadata(records: Iterable[Dict[str, Any]]) -> List[BindingMetadata]:
    bindings: List[BindingMetadata] = []
    names = set()
    indexes = set()
    for index, record in enumerate(records):
        name = str(record.get("name", "")).strip()
        shape = tuple(int(value) for value in record.get("shape", ()))
        dtype = str(record.get("dtype", "")).strip()
        binding_index = int(record.get("index", index))
        if (
            not name
            or name in names
            or binding_index in indexes
            or not shape
            or not dtype
        ):
            raise ValueError("TensorRT binding metadata is incomplete or duplicated")
        names.add(name)
        indexes.add(binding_index)
        bindings.append(
            BindingMetadata(
                index=binding_index,
                name=name,
                is_input=bool(record.get("is_input", False)),
                dtype=dtype,
                shape=shape,
                dynamic=any(value < 0 for value in shape),
            )
        )
    if sum(1 for item in bindings if item.is_input) != 1:
        raise ValueError("Direct TensorRT runtime requires exactly one input binding")
    if not any(not item.is_input for item in bindings):
        raise ValueError("TensorRT engine has no output binding")
    if sorted(indexes) != list(range(len(bindings))):
        raise ValueError("TensorRT binding indexes must be contiguous from zero")
    return bindings


def validate_binding_contract(
    actual: Sequence[BindingMetadata], expected: Dict[str, Sequence[int]]
) -> None:
    actual_by_name = {item.name: item for item in actual}
    if set(actual_by_name) != set(expected):
        raise ValueError(
            "TensorRT binding names differ from the audited ONNX contract: actual={} expected={}".format(
                sorted(actual_by_name), sorted(expected)
            )
        )
    for name, expected_shape in expected.items():
        shape = actual_by_name[name].shape
        expected_tuple = tuple(int(value) for value in expected_shape)
        if shape != expected_tuple:
            raise ValueError(
                "TensorRT binding {} shape differs: actual={} expected={}".format(
                    name, shape, expected_tuple
                )
            )


def _sha256_read_only(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_engine_path(model_config: Dict[str, Any]) -> Path:
    configured_path = model_config.get("engine_path")
    if configured_path:
        return Path(str(configured_path)).expanduser().resolve()
    engine_dir_value = model_config.get(
        "engine_dir", "/home/dji/gap_plot_ai_assets/models/engine"
    )
    engine_dir = Path(str(engine_dir_value)).expanduser().resolve()
    if not engine_dir.is_dir():
        raise FileNotFoundError(
            "Protected TensorRT engine directory not found: {}".format(engine_dir)
        )
    candidates = sorted(engine_dir.glob("*.engine"))
    if len(candidates) != 1:
        raise FileNotFoundError(
            "Expected exactly one read-only detector .engine under {}, found {}".format(
                engine_dir, len(candidates)
            )
        )
    return candidates[0].resolve()


class CudaError(RuntimeError):
    pass


class CudaRuntime:
    def __init__(self, device_index: int = 0) -> None:
        candidates = (
            "libcudart.so",
            "libcudart.so.11.0",
            "/usr/local/cuda-11.4/lib64/libcudart.so",
        )
        self.lib = None
        errors = []
        for candidate in candidates:
            try:
                self.lib = ctypes.CDLL(candidate)
                break
            except OSError as error:
                errors.append(str(error))
        if self.lib is None:
            raise CudaError("CUDA 11.4 runtime library could not be loaded")
        self.lib.cudaGetErrorString.argtypes = [ctypes.c_int]
        self.lib.cudaGetErrorString.restype = ctypes.c_char_p
        self.lib.cudaSetDevice.argtypes = [ctypes.c_int]
        self.lib.cudaStreamCreate.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        self.lib.cudaStreamDestroy.argtypes = [ctypes.c_void_p]
        self.lib.cudaStreamSynchronize.argtypes = [ctypes.c_void_p]
        self.lib.cudaMalloc.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t]
        self.lib.cudaFree.argtypes = [ctypes.c_void_p]
        self.lib.cudaHostAlloc.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_size_t,
            ctypes.c_uint,
        ]
        self.lib.cudaFreeHost.argtypes = [ctypes.c_void_p]
        self.lib.cudaMemcpyAsync.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        for name in (
            "cudaStreamCreate",
            "cudaStreamDestroy",
            "cudaStreamSynchronize",
            "cudaMalloc",
            "cudaFree",
            "cudaHostAlloc",
            "cudaFreeHost",
            "cudaMemcpyAsync",
            "cudaSetDevice",
        ):
            getattr(self.lib, name).restype = ctypes.c_int
        self._check(self.lib.cudaSetDevice(int(device_index)), "cudaSetDevice")

    def _check(self, code: int, operation: str) -> None:
        if code == 0:
            return
        message = self.lib.cudaGetErrorString(code)
        decoded = message.decode("utf-8", "replace") if message else "unknown"
        raise CudaError("{} failed: CUDA {} ({})".format(operation, code, decoded))

    def stream_create(self) -> ctypes.c_void_p:
        stream = ctypes.c_void_p()
        self._check(self.lib.cudaStreamCreate(ctypes.byref(stream)), "cudaStreamCreate")
        return stream

    def stream_destroy(self, stream: ctypes.c_void_p) -> None:
        self._check(self.lib.cudaStreamDestroy(stream), "cudaStreamDestroy")

    def stream_synchronize(self, stream: ctypes.c_void_p) -> None:
        self._check(self.lib.cudaStreamSynchronize(stream), "cudaStreamSynchronize")

    def device_alloc(self, size: int) -> ctypes.c_void_p:
        pointer = ctypes.c_void_p()
        self._check(self.lib.cudaMalloc(ctypes.byref(pointer), size), "cudaMalloc")
        return pointer

    def device_free(self, pointer: ctypes.c_void_p) -> None:
        self._check(self.lib.cudaFree(pointer), "cudaFree")

    def host_alloc(self, size: int) -> ctypes.c_void_p:
        pointer = ctypes.c_void_p()
        self._check(self.lib.cudaHostAlloc(ctypes.byref(pointer), size, 0), "cudaHostAlloc")
        return pointer

    def host_free(self, pointer: ctypes.c_void_p) -> None:
        self._check(self.lib.cudaFreeHost(pointer), "cudaFreeHost")

    def memcpy_async(
        self,
        destination: ctypes.c_void_p,
        source: ctypes.c_void_p,
        size: int,
        kind: int,
        stream: ctypes.c_void_p,
    ) -> None:
        self._check(
            self.lib.cudaMemcpyAsync(destination, source, size, kind, stream),
            "cudaMemcpyAsync",
        )


class BindingBuffer:
    def __init__(
        self, cuda: CudaRuntime, metadata: BindingMetadata, dtype: np.dtype
    ) -> None:
        self.cuda = cuda
        self.metadata = metadata
        self.dtype = np.dtype(dtype)
        self.size = int(np.prod(metadata.shape))
        self.nbytes = self.size * self.dtype.itemsize
        self.host_pointer = cuda.host_alloc(self.nbytes)
        self._host_owner = (ctypes.c_ubyte * self.nbytes).from_address(
            int(self.host_pointer.value)
        )
        self.host = np.ctypeslib.as_array(self._host_owner).view(self.dtype).reshape(
            metadata.shape
        )
        try:
            self.device_pointer = cuda.device_alloc(self.nbytes)
        except Exception:
            cuda.host_free(self.host_pointer)
            raise
        self.closed = False

    @property
    def address(self) -> int:
        return int(self.device_pointer.value)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.cuda.device_free(self.device_pointer)
        self.cuda.host_free(self.host_pointer)


def _opencv_nms(
    boxes_xyxy: np.ndarray,
    scores: np.ndarray,
    classes: np.ndarray,
    iou_threshold: float,
    max_detections: int,
) -> np.ndarray:
    kept: List[int] = []
    for class_id in sorted(set(int(value) for value in classes.tolist())):
        indexes = np.flatnonzero(classes == class_id)
        if indexes.size == 0:
            continue
        boxes_xywh = [
            [
                float(boxes_xyxy[index, 0]),
                float(boxes_xyxy[index, 1]),
                float(boxes_xyxy[index, 2] - boxes_xyxy[index, 0]),
                float(boxes_xyxy[index, 3] - boxes_xyxy[index, 1]),
            ]
            for index in indexes
        ]
        # Do not use max_detections as OpenCV top_k here.
        # All confidence-qualified candidates must enter NMS.
        selected = cv2.dnn.NMSBoxes(
            boxes_xywh,
            [float(scores[index]) for index in indexes],
            0.0,
            float(iou_threshold),
        )
        if selected is None:
            continue
        for local_index in np.asarray(selected).reshape(-1).tolist():
            kept.append(int(indexes[int(local_index)]))
    kept.sort(key=lambda index: float(scores[index]), reverse=True)
    return np.asarray(kept[:max_detections], dtype=np.int64)


class DirectTensorRTModel:
    """TensorRT 8.5 runtime with one persistent context and persistent buffers."""

    def __init__(
        self,
        model_config: Dict[str, Any],
        *,
        backend: str,
        device: Union[str, int],
    ) -> None:
        if backend != "engine":
            raise ValueError("DirectTensorRTModel only accepts backend=engine")
        if str(model_config.get("task")) != "detect":
            raise ValueError("Direct TensorRT live MVP currently supports detector only")
        self.config = model_config
        self.path = _resolve_engine_path(model_config)
        if not self.path.is_file():
            raise FileNotFoundError("TensorRT engine not found: {}".format(self.path))
        self.backend = "engine"
        self.device = device
        if str(device) not in {"0", "cuda:0"}:
            raise ValueError("Direct TensorRT runtime is pinned to cuda:0")
        self.expected_task = "detect"
        self.names = {
            int(key): str(value)
            for key, value in model_config.get("class_names", {}).items()
        }
        target = model_config.get("tensorrt", {})
        if target.get("output_decoder") != "ultralytics_v8_xywh_class_scores":
            raise ValueError("TensorRT output_decoder is not explicitly supported")
        expected_bindings = target.get("expected_bindings")
        if not isinstance(expected_bindings, dict) or not expected_bindings:
            raise ValueError("TensorRT expected_bindings must come from audited ONNX metadata")

        import tensorrt as trt

        self.trt = trt
        self.sha256 = _sha256_read_only(self.path)
        self.engine_size_bytes = self.path.stat().st_size
        self.logger = trt.Logger(trt.Logger.WARNING)
        trt.init_libnvinfer_plugins(self.logger, "")
        self.runtime = trt.Runtime(self.logger)
        with self.path.open("rb") as source:
            serialized = source.read()
        self.engine = self.runtime.deserialize_cuda_engine(serialized)
        if self.engine is None:
            raise RuntimeError("TensorRT 8.5.2 could not deserialize the engine")
        self.context = self.engine.create_execution_context()
        if self.context is None:
            raise RuntimeError("TensorRT execution context creation failed")
        raw_metadata = []
        for index in range(self.engine.num_bindings):
            raw_metadata.append(
                {
                    "index": index,
                    "name": self.engine.get_binding_name(index),
                    "is_input": self.engine.binding_is_input(index),
                    "dtype": str(self.engine.get_binding_dtype(index)),
                    "shape": tuple(self.engine.get_binding_shape(index)),
                }
            )
        initial = normalize_binding_metadata(raw_metadata)
        input_binding = next(item for item in initial if item.is_input)
        if input_binding.dynamic:
            configured_shape = target.get("input_shape")
            if not isinstance(configured_shape, list):
                raise ValueError("dynamic TensorRT input requires tensorrt.input_shape")
            if not self.context.set_binding_shape(
                input_binding.index, tuple(int(value) for value in configured_shape)
            ):
                raise ValueError("TensorRT rejected the configured dynamic input shape")
        resolved_records = []
        for item in initial:
            resolved_records.append(
                {
                    "index": item.index,
                    "name": item.name,
                    "is_input": item.is_input,
                    "dtype": item.dtype,
                    "shape": tuple(self.context.get_binding_shape(item.index)),
                }
            )
        self.bindings = normalize_binding_metadata(resolved_records)
        if any(item.dynamic for item in self.bindings):
            raise ValueError("TensorRT binding shape remains unresolved")
        validate_binding_contract(self.bindings, expected_bindings)

        self.cuda = CudaRuntime(device_index=0)
        self.stream = self.cuda.stream_create()
        self.buffers: Dict[int, BindingBuffer] = {}
        self.binding_addresses = [0] * len(self.bindings)
        try:
            for item in self.bindings:
                dtype = np.dtype(trt.nptype(self.engine.get_binding_dtype(item.index)))
                buffer = BindingBuffer(self.cuda, item, dtype)
                self.buffers[item.index] = buffer
                self.binding_addresses[item.index] = buffer.address
        except Exception:
            for buffer in self.buffers.values():
                buffer.close()
            self.cuda.stream_destroy(self.stream)
            raise
        self.input_binding = next(item for item in self.bindings if item.is_input)
        self.output_bindings = [item for item in self.bindings if not item.is_input]
        self._backend_initialization_count = 1
        self._closed = False

    def audit(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "backend": "direct_tensorrt",
            "task": self.expected_task,
            "class_names": self.names,
            "sha256": self.sha256,
            "engine_size_bytes": self.engine_size_bytes,
            "tensorrt_version": str(self.trt.__version__),
            "bindings": [
                {
                    "index": item.index,
                    "name": item.name,
                    "is_input": item.is_input,
                    "dtype": item.dtype,
                    "shape": list(item.shape),
                }
                for item in self.bindings
            ],
            "pinned_host_memory": True,
            "buffers_reused": True,
            "backend_initialization_count": self._backend_initialization_count,
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
        }

    def warmup(self) -> ModelOutput:
        shape = self.input_binding.shape
        height, width = self._input_height_width(shape)
        output = self.predict(np.zeros((height, width, 3), dtype=np.uint8))
        if output.warning:
            raise RuntimeError("TensorRT warm-up failed: {}".format(output.warning))
        return output

    @staticmethod
    def _input_height_width(shape: Sequence[int]) -> Tuple[int, int]:
        if len(shape) != 4 or shape[0] != 1:
            raise ValueError("TensorRT input must be batch-1 rank-4")
        if shape[1] == 3:
            return int(shape[2]), int(shape[3])
        if shape[3] == 3:
            return int(shape[1]), int(shape[2])
        raise ValueError("TensorRT input must have exactly three color channels")

    def _decode_detector(
        self, output: np.ndarray, meta: Any
    ) -> List[Dict[str, Any]]:
        array = np.asarray(output)
        if array.ndim == 3 and array.shape[0] == 1:
            array = array[0]
        feature_count = 4 + len(self.names)
        if array.ndim != 2:
            raise ValueError("detector output must be rank-2 after removing batch")
        if array.shape[0] == feature_count:
            predictions = array.T
        elif array.shape[1] == feature_count:
            predictions = array
        else:
            raise ValueError(
                "detector output does not match xywh + class-score layout"
            )
        boxes_xywh = predictions[:, :4].astype(np.float32, copy=False)
        class_scores = predictions[:, 4:].astype(np.float32, copy=False)
        classes = np.argmax(class_scores, axis=1).astype(np.int32)
        scores = class_scores[np.arange(class_scores.shape[0]), classes]
        mask = np.isfinite(scores) & (scores >= float(self.config["confidence_threshold"]))
        boxes_xywh = boxes_xywh[mask]
        classes = classes[mask]
        scores = scores[mask]
        if boxes_xywh.size == 0:
            return []
        boxes_xyxy = np.empty_like(boxes_xywh)
        boxes_xyxy[:, 0] = boxes_xywh[:, 0] - boxes_xywh[:, 2] / 2.0
        boxes_xyxy[:, 1] = boxes_xywh[:, 1] - boxes_xywh[:, 3] / 2.0
        boxes_xyxy[:, 2] = boxes_xywh[:, 0] + boxes_xywh[:, 2] / 2.0
        boxes_xyxy[:, 3] = boxes_xywh[:, 1] + boxes_xywh[:, 3] / 2.0
        keep = _opencv_nms(
            boxes_xyxy,
            scores,
            classes,
            float(self.config["nms_iou_threshold"]),
            int(self.config["max_detections"]),
        )
        detections: List[Dict[str, Any]] = []
        for index in keep.tolist():
            restored = reverse_letterbox_bbox(boxes_xyxy[index], meta)
            if restored is None:
                continue
            class_id = int(classes[index])
            detections.append(
                {
                    "bbox_xyxy": [float(value) for value in restored],
                    "confidence": float(scores[index]),
                    "class_id": class_id,
                    "class_name": self.names.get(class_id, str(class_id)),
                }
            )
        return detections

    def predict(self, image_bgr: np.ndarray) -> ModelOutput:
        if self._closed:
            return ModelOutput(warning="TensorRT backend is closed")
        started = time.perf_counter()
        try:
            input_height, input_width = self._input_height_width(self.input_binding.shape)
            preprocessing_started = time.perf_counter()
            tensor, meta = preprocess_rgb_chw(
                image_bgr, (input_height, input_width)
            )
            if self.input_binding.shape[-1] == 3:
                tensor = tensor.transpose(0, 2, 3, 1)
            input_buffer = self.buffers[self.input_binding.index]
            np.copyto(input_buffer.host, tensor.astype(input_buffer.dtype, copy=False))
            preprocessing_ms = (time.perf_counter() - preprocessing_started) * 1000

            inference_started = time.perf_counter()
            self.cuda.memcpy_async(
                input_buffer.device_pointer,
                input_buffer.host_pointer,
                input_buffer.nbytes,
                CUDA_MEMCPY_HOST_TO_DEVICE,
                self.stream,
            )
            success = self.context.execute_async_v2(
                bindings=self.binding_addresses,
                stream_handle=int(self.stream.value),
            )
            if not success:
                raise RuntimeError("TensorRT execute_async_v2 returned false")
            for binding in self.output_bindings:
                buffer = self.buffers[binding.index]
                self.cuda.memcpy_async(
                    buffer.host_pointer,
                    buffer.device_pointer,
                    buffer.nbytes,
                    CUDA_MEMCPY_DEVICE_TO_HOST,
                    self.stream,
                )
            self.cuda.stream_synchronize(self.stream)
            inference_ms = (time.perf_counter() - inference_started) * 1000

            post_started = time.perf_counter()
            if len(self.output_bindings) != 1:
                raise ValueError("detector engine must expose exactly one output")
            output = self.buffers[self.output_bindings[0].index].host
            detections = self._decode_detector(output, meta)
            postprocessing_ms = (time.perf_counter() - post_started) * 1000
            return ModelOutput(
                detections=detections,
                latency_ms=(time.perf_counter() - started) * 1000,
                preprocessing_ms=preprocessing_ms,
                inference_ms=inference_ms,
                postprocessing_ms=postprocessing_ms,
            )
        except Exception as error:
            return ModelOutput(
                latency_ms=(time.perf_counter() - started) * 1000,
                warning="{}: {}".format(type(error).__name__, error),
            )

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors = []
        for buffer in self.buffers.values():
            try:
                buffer.close()
            except Exception as error:
                errors.append(error)
        try:
            self.cuda.stream_destroy(self.stream)
        except Exception as error:
            errors.append(error)
        self.context = None
        self.engine = None
        self.runtime = None
        if errors:
            raise RuntimeError("TensorRT shutdown reported {} CUDA errors".format(len(errors)))



class DirectTensorRTSegmenterModel:
    """TensorRT 8.5 segmenter with mask prototype decoding (NumPy only)."""

    def __init__(
        self,
        model_config: Dict[str, Any],
        *,
        backend: str,
        device: Union[str, int],
    ) -> None:
        if backend != "engine":
            raise ValueError("DirectTensorRTSegmenterModel only accepts backend=engine")
        if str(model_config.get("task")) != "segment":
            raise ValueError(
                "DirectTensorRTSegmenterModel expects task=segment, got {}".format(
                    model_config.get("task")
                )
            )
        self.config = model_config
        self.path = _resolve_engine_path(model_config)
        if not self.path.is_file():
            raise FileNotFoundError("TensorRT engine not found: {}".format(self.path))
        self.backend = "engine"
        self.device = device
        if str(device) not in {"0", "cuda:0"}:
            raise ValueError("Direct TensorRT runtime is pinned to cuda:0")
        self.expected_task = "segment"
        self.names = {
            int(key): str(value)
            for key, value in model_config.get("class_names", {}).items()
        }

        import tensorrt as trt

        self.trt = trt
        self.sha256 = _sha256_read_only(self.path)
        self.engine_size_bytes = self.path.stat().st_size
        self.logger = trt.Logger(trt.Logger.WARNING)
        trt.init_libnvinfer_plugins(self.logger, "")
        self.runtime = trt.Runtime(self.logger)
        with self.path.open("rb") as source:
            serialized = source.read()
        self.engine = self.runtime.deserialize_cuda_engine(serialized)
        if self.engine is None:
            raise RuntimeError("TensorRT 8.5.2 could not deserialize the engine")
        self.context = self.engine.create_execution_context()
        if self.context is None:
            raise RuntimeError("TensorRT execution context creation failed")

        raw_metadata = []
        for index in range(self.engine.num_bindings):
            raw_metadata.append(
                {
                    "index": index,
                    "name": self.engine.get_binding_name(index),
                    "is_input": self.engine.binding_is_input(index),
                    "dtype": str(self.engine.get_binding_dtype(index)),
                    "shape": tuple(self.engine.get_binding_shape(index)),
                }
            )
        self.bindings = normalize_binding_metadata(raw_metadata)
        if any(item.dynamic for item in self.bindings):
            raise ValueError("Segmenter engine has unresolved dynamic shape")

        self.cuda = CudaRuntime(device_index=0)
        self.stream = self.cuda.stream_create()
        self.buffers: Dict[int, BindingBuffer] = {}
        self.binding_addresses = [0] * len(self.bindings)
        try:
            for item in self.bindings:
                dtype = np.dtype(trt.nptype(self.engine.get_binding_dtype(item.index)))
                buffer = BindingBuffer(self.cuda, item, dtype)
                self.buffers[item.index] = buffer
                self.binding_addresses[item.index] = buffer.address
        except Exception:
            for buffer in self.buffers.values():
                buffer.close()
            self.cuda.stream_destroy(self.stream)
            raise
        self.input_binding = next(item for item in self.bindings if item.is_input)
        self.output_bindings = [item for item in self.bindings if not item.is_input]
        if len(self.output_bindings) != 2:
            raise ValueError(
                "Segmenter engine must expose exactly 2 outputs (detection + prototypes), got {}".format(
                    len(self.output_bindings)
                )
            )
        self._backend_initialization_count = 1
        self._closed = False

        self._image_size = int(self.config["image_size"])
        self._conf_threshold = float(self.config["confidence_threshold"])
        self._nms_iou = float(self.config["nms_iou_threshold"])
        self._max_detections = int(self.config["max_detections"])
        self._morph_open = int(self.config.get("morphology_open_kernel", 0))
        self._morph_close = int(self.config.get("morphology_close_kernel", 0))
        self._contour_min_area = float(self.config.get("contour_min_area_px", 256))
        self._contour_epsilon = float(self.config.get("contour_epsilon_ratio", 0.002))
        self._contour_max_points = int(self.config.get("contour_max_points", 96))

    def audit(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "backend": "direct_tensorrt_segmenter",
            "task": self.expected_task,
            "class_names": self.names,
            "sha256": self.sha256,
            "engine_size_bytes": self.engine_size_bytes,
            "tensorrt_version": str(self.trt.__version__),
            "bindings": [
                {
                    "index": item.index,
                    "name": item.name,
                    "is_input": item.is_input,
                    "dtype": item.dtype,
                    "shape": list(item.shape),
                }
                for item in self.bindings
            ],
            "pinned_host_memory": True,
            "buffers_reused": True,
            "backend_initialization_count": self._backend_initialization_count,
            "image_size": self._image_size,
            "confidence_threshold": self._conf_threshold,
            "nms_iou_threshold": self._nms_iou,
            "morphology_open_kernel": self._morph_open,
            "morphology_close_kernel": self._morph_close,
        }

    def warmup(self) -> ModelOutput:
        shape = self.input_binding.shape
        height, width = self._input_height_width(shape)
        output = self.predict(np.zeros((height, width, 3), dtype=np.uint8))
        if output.warning:
            raise RuntimeError(
                "TensorRT segmenter warm-up failed: {}".format(output.warning)
            )
        return output

    @staticmethod
    def _input_height_width(shape: Sequence[int]) -> Tuple[int, int]:
        if len(shape) != 4 or shape[0] != 1:
            raise ValueError("TensorRT input must be batch-1 rank-4")
        if shape[1] == 3:
            return int(shape[2]), int(shape[3])
        if shape[3] == 3:
            return int(shape[1]), int(shape[2])
        raise ValueError("TensorRT input must have exactly three color channels")

    @staticmethod
    def _sigmoid(x: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-np.clip(x, -88.0, 88.0)))

    def _decode_segmenter(
        self,
        det_output: np.ndarray,
        proto_output: np.ndarray,
        meta: Any,
    ) -> Tuple[np.ndarray, List[List[List[float]]]]:
        """Decode YOLOv8-seg raw outputs into plantable-area mask + contours."""
        height, width = self._input_height_width(self.input_binding.shape)

        # output0: (1, 37, 33600) -> (33600, 37)
        det = np.asarray(det_output)
        if det.ndim == 3 and det.shape[0] == 1:
            det = det[0]
        num_features = 4 + 1 + 32  # bbox + class + mask_coeffs
        if det.shape[0] == num_features:
            det = det.T
        elif det.shape[1] != num_features:
            raise ValueError(
                "Segmenter detection output has {} features, expected {}".format(
                    det.shape[1] if det.ndim == 2 else det.shape, num_features
                )
            )

        boxes_xywh = det[:, :4].astype(np.float32, copy=False)  # (N, 4)
        class_scores = det[:, 4:5].astype(np.float32, copy=False)  # (N, 1)
        mask_coeffs = det[:, 5:37].astype(np.float32, copy=False)  # (N, 32)

        scores = class_scores[:, 0]
        conf_mask = (
            np.isfinite(scores)
            & (scores >= self._conf_threshold)
        )
        boxes_xywh = boxes_xywh[conf_mask]
        mask_coeffs = mask_coeffs[conf_mask]
        scores = scores[conf_mask]
        classes = np.zeros(len(scores), dtype=np.int32)  # single class

        if boxes_xywh.size == 0:
            return np.zeros((height, width), dtype=np.uint8), []

        # xywh -> xyxy
        boxes_xyxy = np.empty_like(boxes_xywh)
        boxes_xyxy[:, 0] = boxes_xywh[:, 0] - boxes_xywh[:, 2] / 2.0
        boxes_xyxy[:, 1] = boxes_xywh[:, 1] - boxes_xywh[:, 3] / 2.0
        boxes_xyxy[:, 2] = boxes_xywh[:, 0] + boxes_xywh[:, 2] / 2.0
        boxes_xyxy[:, 3] = boxes_xywh[:, 1] + boxes_xywh[:, 3] / 2.0

        keep = _opencv_nms(
            boxes_xyxy, scores, classes,
            self._nms_iou, self._max_detections,
        )

        if keep.size == 0:
            return np.zeros((height, width), dtype=np.uint8), []

        boxes_xyxy = boxes_xyxy[keep]
        mask_coeffs = mask_coeffs[keep]

        # Mask prototypes: (32, 320, 320)
        protos = np.asarray(proto_output, dtype=np.float32)
        if protos.ndim == 4 and protos.shape[0] == 1:
            protos = protos[0]
        num_protos, proto_h, proto_w = protos.shape

        # Decode each mask: coeffs @ protos -> sigmoid -> threshold
        protos_flat = protos.reshape(num_protos, -1)  # (32, proto_h * proto_w)
        masks_raw = mask_coeffs @ protos_flat  # (M, proto_h * proto_w)
        masks_sig = self._sigmoid(masks_raw.reshape(-1, proto_h, proto_w))  # (M, proto_h, proto_w)

        # Union all instance masks
        combined = np.max(masks_sig, axis=0)  # (proto_h, proto_w)
        combined = np.nan_to_num(combined, nan=0.0, posinf=0.0, neginf=0.0)
        binary = (combined >= 0.5).astype(np.uint8)

        # Resize prototype mask to model input size (letterbox space)
        binary_input_size = cv2.resize(
            binary, (width, height), interpolation=cv2.INTER_LINEAR
        )
        binary_input_size = (binary_input_size >= 0.5).astype(np.uint8)

        # Reverse letterbox: crop padding, scale back to original image
        source_h = meta.source_height
        source_w = meta.source_width
        pad_top = meta.pad_top
        pad_left = meta.pad_left
        scale = meta.scale

        # Crop the valid (non-padded) region from letterbox
        resized_h = int(round(source_h * scale))
        resized_w = int(round(source_w * scale))
        cropped = binary_input_size[pad_top:pad_top + resized_h, pad_left:pad_left + resized_w]

        # Resize to original frame size
        mask = cv2.resize(cropped, (source_w, source_h), interpolation=cv2.INTER_LINEAR)
        mask = (mask >= 0.5).astype(np.uint8)

        # Morphology
        if self._morph_open > 1:
            kernel = np.ones((self._morph_open, self._morph_open), dtype=np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        if self._morph_close > 1:
            kernel = np.ones((self._morph_close, self._morph_close), dtype=np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        # Contours
        from .geometry import mask_to_contours
        contours = mask_to_contours(
            mask,
            min_area_px=self._contour_min_area,
            epsilon_ratio=self._contour_epsilon,
            max_points=self._contour_max_points,
        )

        return mask, contours

    def predict(self, image_bgr: np.ndarray) -> ModelOutput:
        if self._closed:
            return ModelOutput(warning="TensorRT segmenter backend is closed")
        started = time.perf_counter()
        try:
            input_height, input_width = self._input_height_width(self.input_binding.shape)
            preprocessing_started = time.perf_counter()
            tensor, meta = preprocess_rgb_chw(
                image_bgr, (input_height, input_width)
            )
            if self.input_binding.shape[-1] == 3:
                tensor = tensor.transpose(0, 2, 3, 1)
            input_buffer = self.buffers[self.input_binding.index]
            np.copyto(input_buffer.host, tensor.astype(input_buffer.dtype, copy=False))
            preprocessing_ms = (time.perf_counter() - preprocessing_started) * 1000

            inference_started = time.perf_counter()
            self.cuda.memcpy_async(
                input_buffer.device_pointer,
                input_buffer.host_pointer,
                input_buffer.nbytes,
                CUDA_MEMCPY_HOST_TO_DEVICE,
                self.stream,
            )
            success = self.context.execute_async_v2(
                bindings=self.binding_addresses,
                stream_handle=int(self.stream.value),
            )
            if not success:
                raise RuntimeError("TensorRT execute_async_v2 returned false")
            for binding in self.output_bindings:
                buffer = self.buffers[binding.index]
                self.cuda.memcpy_async(
                    buffer.host_pointer,
                    buffer.device_pointer,
                    buffer.nbytes,
                    CUDA_MEMCPY_DEVICE_TO_HOST,
                    self.stream,
                )
            self.cuda.stream_synchronize(self.stream)
            inference_ms = (time.perf_counter() - inference_started) * 1000

            post_started = time.perf_counter()
            det_output = self.buffers[self.output_bindings[0].index].host
            proto_output = self.buffers[self.output_bindings[1].index].host
            mask, contours = self._decode_segmenter(
                det_output, proto_output, meta
            )
            postprocessing_ms = (time.perf_counter() - post_started) * 1000

            return ModelOutput(
                mask=mask,
                contours=contours,
                latency_ms=(time.perf_counter() - started) * 1000,
                preprocessing_ms=preprocessing_ms,
                inference_ms=inference_ms,
                postprocessing_ms=postprocessing_ms,
            )
        except Exception as error:
            return ModelOutput(
                latency_ms=(time.perf_counter() - started) * 1000,
                warning="{}: {}".format(type(error).__name__, error),
            )

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors = []
        for buffer in self.buffers.values():
            try:
                buffer.close()
            except Exception as error:
                errors.append(error)
        try:
            self.cuda.stream_destroy(self.stream)
        except Exception as error:
            errors.append(error)
        self.context = None
        self.engine = None
        self.runtime = None
        if errors:
            raise RuntimeError(
                "TensorRT segmenter shutdown reported {} CUDA errors".format(
                    len(errors)
                )
            )
