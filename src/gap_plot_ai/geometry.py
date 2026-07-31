from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple, Union

import cv2
import numpy as np


@dataclass(frozen=True)
class LetterboxMeta:
    source_width: int
    source_height: int
    target_width: int
    target_height: int
    scale: float
    pad_left: int
    pad_top: int


def letterbox(
    image: np.ndarray,
    target_size: Union[int, Tuple[int, int]],
    color: Tuple[int, int, int] = (114, 114, 114),
    scale_up: bool = True,
) -> Tuple[np.ndarray, LetterboxMeta]:
    if image.ndim != 3 or image.shape[2] != 3 or image.size == 0:
        raise ValueError("image harus berupa HxWx3 non-empty")
    target_h, target_w = (
        (target_size, target_size) if isinstance(target_size, int) else target_size
    )
    if target_h <= 0 or target_w <= 0:
        raise ValueError("target_size harus positif")
    source_h, source_w = image.shape[:2]
    scale = min(target_w / source_w, target_h / source_h)
    if not scale_up:
        scale = min(scale, 1.0)
    resized_w = max(1, int(round(source_w * scale)))
    resized_h = max(1, int(round(source_h * scale)))
    resized = cv2.resize(image, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
    pad_w = target_w - resized_w
    pad_h = target_h - resized_h
    left = pad_w // 2
    right = pad_w - left
    top = pad_h // 2
    bottom = pad_h - top
    output = cv2.copyMakeBorder(
        resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color
    )
    return output, LetterboxMeta(
        source_width=source_w,
        source_height=source_h,
        target_width=target_w,
        target_height=target_h,
        scale=scale,
        pad_left=left,
        pad_top=top,
    )


def preprocess_rgb_chw(
    image_bgr: np.ndarray, target_size: int
) -> Tuple[np.ndarray, LetterboxMeta]:
    padded, meta = letterbox(image_bgr, target_size)
    rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
    tensor = np.ascontiguousarray(rgb.transpose(2, 0, 1), dtype=np.float32) / 255.0
    return tensor[None, ...], meta


def clip_bbox(
    bbox: Iterable[float], width: int, height: int
) -> Optional[Tuple[float, float, float, float]]:
    values = list(bbox)
    if len(values) != 4 or width <= 0 or height <= 0:
        return None
    if not all(np.isfinite(value) for value in values):
        return None
    x1, y1, x2, y2 = values
    x1 = float(np.clip(x1, 0, width))
    y1 = float(np.clip(y1, 0, height))
    x2 = float(np.clip(x2, 0, width))
    y2 = float(np.clip(y2, 0, height))
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def reverse_letterbox_bbox(
    bbox: Iterable[float], meta: LetterboxMeta
) -> Optional[Tuple[float, float, float, float]]:
    values = list(bbox)
    if len(values) != 4 or meta.scale <= 0:
        return None
    x1, y1, x2, y2 = values
    restored = (
        (x1 - meta.pad_left) / meta.scale,
        (y1 - meta.pad_top) / meta.scale,
        (x2 - meta.pad_left) / meta.scale,
        (y2 - meta.pad_top) / meta.scale,
    )
    return clip_bbox(restored, meta.source_width, meta.source_height)


def mask_to_contours(
    mask: np.ndarray,
    *,
    min_area_px: float,
    epsilon_ratio: float,
    max_points: int,
) -> List[List[List[float]]]:
    if mask.ndim != 2 or mask.size == 0 or max_points < 3:
        return []
    binary = np.ascontiguousarray(mask > 0, dtype=np.uint8) * 255
    found, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    contours: List[List[List[float]]] = []
    for contour in sorted(found, key=cv2.contourArea, reverse=True):
        if cv2.contourArea(contour) < min_area_px:
            continue
        perimeter = cv2.arcLength(contour, True)
        epsilon = max(0.0, float(epsilon_ratio)) * perimeter
        simplified = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
        if simplified.shape[0] > max_points:
            indexes = np.linspace(0, simplified.shape[0] - 1, max_points, dtype=int)
            simplified = simplified[indexes]
        if simplified.shape[0] < 3:
            continue
        contours.append([[float(x), float(y)] for x, y in simplified])
    return contours


def rasterize_contours(
    contours: List[List[List[float]]], width: int, height: int
) -> np.ndarray:
    output = np.zeros((height, width), dtype=np.uint8)
    polygons = []
    for contour in contours:
        array = np.asarray(contour, dtype=np.float32)
        if array.ndim == 2 and array.shape[0] >= 3 and array.shape[1] == 2:
            polygons.append(np.rint(array).astype(np.int32))
    if polygons:
        cv2.fillPoly(output, polygons, 1)
    return output
