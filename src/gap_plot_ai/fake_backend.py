from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

import numpy as np

from .schema import ModelOutput


class FakeModel:
    """Explicit orchestration-only backend; never used by the device launcher."""

    instances_created = 0

    def __init__(self, config: Dict[str, Any], *, backend: str, device: Any):
        del backend, device
        type(self).instances_created += 1
        self.config = config
        self.path = Path(f"fake_{config['task']}.model")
        self.sha256 = "fake-backend-no-tensorrt"
        self.predict_count = 0
        self.warmup_count = 0
        self._backend_initialization_count = 0
        self.delay_seconds = float(config.get("fake_delay_seconds", 0))

    def audit(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "backend": "fake",
            "verified_tensorrt": False,
            "backend_initialization_count": self._backend_initialization_count,
        }

    def warmup(self) -> ModelOutput:
        self.warmup_count += 1
        if self._backend_initialization_count == 0:
            self._backend_initialization_count = 1
        return ModelOutput()

    def predict(self, image: np.ndarray) -> ModelOutput:
        self.predict_count += 1
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        height, width = image.shape[:2]
        if self.config["task"] == "detect":
            return ModelOutput(
                detections=[
                    {
                        "bbox_xyxy": [
                            width * 0.4,
                            height * 0.4,
                            width * 0.6,
                            height * 0.6,
                        ],
                        "confidence": 0.9,
                        "class_id": 0,
                        "class_name": "plant",
                    }
                ],
                latency_ms=self.delay_seconds * 1000,
            )
        return ModelOutput(
            contours=[
                [
                    [0.0, 0.0],
                    [float(width - 1), 0.0],
                    [float(width - 1), float(height - 1)],
                ]
            ],
            latency_ms=self.delay_seconds * 1000,
        )

    def shutdown(self) -> None:
        return None
