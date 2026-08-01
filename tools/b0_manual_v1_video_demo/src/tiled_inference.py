from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class TileWindow:
    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class Detection:
    class_id: int
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    center_x: float
    center_y: float
    source_tile_x: int
    source_tile_y: int
    source_tile_width: int
    source_tile_height: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _axis_positions(origin: int, length: int, tile_size: int, overlap: int) -> list[int]:
    if length <= tile_size:
        return [origin]
    stride = tile_size - overlap
    last = origin + length - tile_size
    positions = list(range(origin, last + 1, stride))
    if positions[-1] != last:
        positions.append(last)
    return positions


def generate_tile_windows(
    frame_width: int,
    frame_height: int,
    tile_size: int,
    overlap: int,
    roi: tuple[int, int, int, int] | None = None,
) -> list[TileWindow]:
    if frame_width <= 0 or frame_height <= 0 or tile_size <= 0:
        raise ValueError("Dimensi frame dan tile_size harus positif.")
    if overlap < 0 or overlap >= tile_size:
        raise ValueError("overlap harus berada di [0, tile_size).")
    if roi is None:
        roi_x, roi_y, roi_width, roi_height = 0, 0, frame_width, frame_height
    else:
        roi_x, roi_y, roi_width, roi_height = map(int, roi)
        if roi_x < 0 or roi_y < 0 or roi_width <= 0 or roi_height <= 0:
            raise ValueError(f"ROI tidak valid: {roi}")
        if roi_x + roi_width > frame_width or roi_y + roi_height > frame_height:
            raise ValueError(f"ROI {roi} keluar dari frame {frame_width}x{frame_height}.")
    xs = _axis_positions(roi_x, roi_width, tile_size, overlap)
    ys = _axis_positions(roi_y, roi_height, tile_size, overlap)
    windows: list[TileWindow] = []
    roi_right, roi_bottom = roi_x + roi_width, roi_y + roi_height
    for y in ys:
        for x in xs:
            width = min(tile_size, roi_right - x, frame_width - x)
            height = min(tile_size, roi_bottom - y, frame_height - y)
            windows.append(TileWindow(x, y, width, height))
    return windows


def local_box_to_global(
    box: tuple[float, float, float, float], tile: TileWindow
) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = box
    return x1 + tile.x, y1 + tile.y, x2 + tile.x, y2 + tile.y


class TiledInferenceEngine:
    def __init__(self, model: Any, config: dict[str, Any]):
        self.model = model
        self.config = config

    def infer_frame(
        self,
        frame: np.ndarray,
        roi: tuple[int, int, int, int] | None,
    ) -> tuple[list[Detection], list[TileWindow], dict[str, float]]:
        height, width = frame.shape[:2]
        windows = generate_tile_windows(
            width,
            height,
            int(self.config["tile_size"]),
            int(self.config["tile_overlap"]),
            roi,
        )
        detections: list[Detection] = []
        tile_times: list[float] = []
        started = time.perf_counter()
        for window in windows:
            tile = frame[
                window.y : window.y + window.height,
                window.x : window.x + window.width,
            ]
            tile_started = time.perf_counter()
            predictions = self.model.predict(tile, self.config)
            tile_times.append((time.perf_counter() - tile_started) * 1000.0)
            for prediction in predictions:
                global_box = local_box_to_global(
                    (
                        prediction["x1"],
                        prediction["y1"],
                        prediction["x2"],
                        prediction["y2"],
                    ),
                    window,
                )
                x1, y1, x2, y2 = global_box
                detections.append(
                    Detection(
                        class_id=int(prediction["class_id"]),
                        class_name=str(prediction["class_name"]),
                        confidence=float(prediction["confidence"]),
                        x1=float(x1),
                        y1=float(y1),
                        x2=float(x2),
                        y2=float(y2),
                        center_x=float((x1 + x2) / 2.0),
                        center_y=float((y1 + y2) / 2.0),
                        source_tile_x=window.x,
                        source_tile_y=window.y,
                        source_tile_width=window.width,
                        source_tile_height=window.height,
                    )
                )
        total_ms = (time.perf_counter() - started) * 1000.0
        timings = {
            "inference_ms": total_ms,
            "mean_tile_ms": float(np.mean(tile_times)) if tile_times else 0.0,
            "min_tile_ms": float(np.min(tile_times)) if tile_times else 0.0,
            "max_tile_ms": float(np.max(tile_times)) if tile_times else 0.0,
        }
        return detections, windows, timings
