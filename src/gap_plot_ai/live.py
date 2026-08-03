from __future__ import annotations

import argparse
import json
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import numpy as np

from .config import load_config, validate_config
from .fake_backend import FakeModel
from .queueing import LatestFrameQueue
from .runtime import InferenceRuntime
from .sources import ArrayFrameSource, FrameEnvelope, FrameSource, ReplayFrameSource
from .state import AppState


class LiveApplication:
    """Hardware-independent Start/Stop supervisor used by replay and tests."""

    def __init__(
        self,
        config: Dict[str, Any],
        source_factory: Callable[[], FrameSource],
        *,
        backend: str = "engine",
        model_factory: Callable[..., Any] = FakeModel,
    ) -> None:
        self.config = config
        self.source_factory = source_factory
        self.runtime = InferenceRuntime(
            config, backend=backend, model_factory=model_factory
        )
        self._queue: Optional[LatestFrameQueue[FrameEnvelope]] = None
        self._source: Optional[FrameSource] = None
        self._source_thread: Optional[threading.Thread] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._source_done = threading.Event()
        self._lock = threading.RLock()
        self.worker_start_count = 0
        self.last_result: Optional[Any] = None

    def start(self) -> bool:
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return False
            self._stop_event.clear()
            self._source_done.clear()
            self._queue = LatestFrameQueue(
                capacity=int(self.config.get("live", {}).get("frame_queue_size", 1))
            )
            self._source = self.source_factory()
            self.runtime.start()
            self._source_thread = threading.Thread(
                target=self._produce, name="gap-plot-replay-source", daemon=True
            )
            self._worker_thread = threading.Thread(
                target=self._consume, name="gap-plot-inference-worker", daemon=True
            )
            self.worker_start_count += 1
            self._source_thread.start()
            self._worker_thread.start()
            return True

    def _produce(self) -> None:
        try:
            assert self._source is not None
            assert self._queue is not None
            for envelope in self._source.frames():
                if self._stop_event.is_set():
                    break
                self._queue.put(envelope)
        except Exception as exc:
            self.runtime.fail(
                "STREAM_DISCONNECTED",
                f"{type(exc).__name__}: {exc}",
                session_status="stream_error",
            )
            self._stop_event.set()
        finally:
            self._source_done.set()

    def _consume(self) -> None:
        assert self._queue is not None
        target_fps = float(self.config.get("live", {}).get("target_inference_fps", 1.5))
        interval = 1.0 / target_fps
        last_started = float("-inf")
        try:
            while not self._stop_event.is_set():
                envelope = self._queue.get(timeout=0.05)
                if envelope is None:
                    if self._source_done.is_set():
                        break
                    continue
                remaining = interval - (time.monotonic() - last_started)
                if remaining > 0 and self._stop_event.wait(remaining):
                    break
                last_started = time.monotonic()
                metadata = envelope.metadata
                self.last_result, _ = self.runtime.infer_frame(
                    envelope.frame_bgr,
                    frame_index=metadata.sequence,
                    capture_timestamp=metadata.capture_timestamp,
                    camera_source=metadata.camera_source,
                    telemetry=envelope.telemetry,
                    source_frame_id=metadata.source_frame_id,
                    capture_monotonic_ns=metadata.capture_monotonic_ns,
                    row_stride=metadata.row_stride,
                    pixel_format=metadata.pixel_format,
                )
        except Exception as exc:
            self.runtime.fail(
                "INFERENCE_WORKER_FAILED",
                f"{type(exc).__name__}: {exc}",
                session_status="inference_error",
            )
        finally:
            if self.runtime.state_machine.state == AppState.RUNNING:
                self.runtime.stop("completed" if self._source_done.is_set() else "stopped")

    def wait(self, timeout: float = 10.0) -> bool:
        worker = self._worker_thread
        if worker is None:
            return True
        worker.join(timeout)
        return not worker.is_alive()

    def stop(self, timeout: float = 10.0) -> bool:
        with self._lock:
            self._stop_event.set()
            if self._source is not None:
                self._source.close()
            if self._queue is not None:
                self._queue.close()
            source_thread = self._source_thread
            worker_thread = self._worker_thread
        if source_thread is not None:
            source_thread.join(timeout)
        if worker_thread is not None:
            worker_thread.join(timeout)
        if worker_thread is not None and worker_thread.is_alive():
            self.runtime.fail(
                "SHUTDOWN_TIMEOUT",
                "Inference worker belum selesai dalam timeout",
                session_status="shutdown_timeout",
            )
            return False
        if self.runtime.state_machine.state not in {AppState.IDLE, AppState.ERROR}:
            self.runtime.stop()
        return True

    def shutdown(self) -> None:
        self.stop()
        self.runtime.shutdown()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gap Plot AI replay integration")
    parser.add_argument("--config", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--video")
    source.add_argument("--synthetic-frames", type=int)
    parser.add_argument("--runtime-root")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = deepcopy(load_config(args.config))
    if args.runtime_root:
        config["runtime"]["root"] = str(Path(args.runtime_root).expanduser().resolve())
    config["models"]["segmenter"]["enabled"] = False
    validate_config(config)
    if args.video:
        source_factory = lambda: ReplayFrameSource(args.video, max_frames=30)
    else:
        if args.synthetic_frames < 1:
            raise ValueError("synthetic-frames minimal 1")
        frames = [
            np.zeros((args.height, args.width, 3), dtype=np.uint8)
            for _ in range(args.synthetic_frames)
        ]
        source_factory = lambda: ArrayFrameSource(frames)
    application = LiveApplication(
        config, source_factory, backend="pt", model_factory=FakeModel
    )
    application.start()
    completed = application.wait(args.timeout)
    queue_stats = application._queue.stats() if application._queue is not None else None
    output = {
        "completed": completed,
        "backend": "fake",
        "tensorrt_verified": False,
        "state": application.runtime.status,
        "model_load_count": application.runtime.model_load_count,
        "warmup_count": application.runtime.warmup_count,
        "backend_initialization_count": application.runtime.backend_initialization_count,
        "worker_start_count": application.worker_start_count,
        "queue": queue_stats.__dict__ if queue_stats is not None else None,
        "last_frame_sequence": (
            application.last_result.frame_index if application.last_result is not None else None
        ),
        "session_summary": (
            str(application.runtime._last_summary)
            if application.runtime._last_summary is not None
            else None
        ),
    }
    application.shutdown()
    print(json.dumps(output, indent=2))
    if not completed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
