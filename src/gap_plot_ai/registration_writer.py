from __future__ import annotations

"""vNext: Asynchronous registration frame writer.
Saves decoded RGB frames for later SIFT/KLT registration.
Non-blocking, bounded queue, background thread."""

import os, time, threading, queue
from pathlib import Path
from typing import Any, Dict, Optional
import cv2
import numpy as np


class RegistrationFrameWriter:
    """Asynchronous writer for registration keyframes.
    Accepts frames from the decoded RGB stream and writes JPEGs in a background thread.
    Never blocks the caller beyond enqueue time."""

    def __init__(
        self,
        session_dir: Path,
        max_queue: int = 16,
        jpeg_quality: int = 92,
        keyframe_interval_ms: float = 800.0,
    ):
        self.session_dir = Path(session_dir)
        self.frames_dir = self.session_dir / "frames" / "registration"
        self.frames_dir.mkdir(parents=True, exist_ok=True)

        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._jpeg_quality = jpeg_quality
        self._keyframe_min_interval = keyframe_interval_ms / 1000.0
        self._last_keyframe_time = 0.0

        self._written = 0
        self._dropped = 0
        self._errors = 0
        self._lock = threading.Lock()

        self._index_entries: list = []

        self._start()

    def _start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="reg_frame_writer")
        self._thread.start()

    def _run(self):
        while not self._stop_event.is_set() or self._queue.qsize() > 0:
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue

            try:
                frame_bgr, frame_id, capture_ns, width, height, is_inference, _ = item

                path = self.frames_dir / ("frame_%010d.jpg" % frame_id)
                ok, encoded = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality])
                if not ok:
                    self._errors += 1
                    continue

                temp = path.with_suffix(".tmp")
                temp.write_bytes(encoded.tobytes())
                os.replace(temp, path)

                entry = {
                    "frame_id": frame_id, "capture_monotonic_ns": capture_ns,
                    "width": width, "height": height, "path": str(path.relative_to(self.session_dir)),
                    "is_inference_frame": is_inference, "size_bytes": int(encoded.nbytes),
                }
                with self._lock:
                    self._index_entries.append(entry)
                    self._written += 1

            except Exception as e:
                self._errors += 1
                import sys; print(f"[FW] write error frame={frame_id}: {e}", file=sys.stderr)

    def submit(self, frame_bgr: np.ndarray, frame_id: int, capture_monotonic_ns: int,
               width: int, height: int, is_inference_frame: bool = False) -> bool:
        """Submit a frame for writing. Returns True if enqueued, False if dropped."""
        now = time.monotonic()
        if not is_inference_frame and now - self._last_keyframe_time < self._keyframe_min_interval:
            self._dropped += 1
            return False
        try:
            self._queue.put_nowait((frame_bgr.copy(), frame_id, capture_monotonic_ns, width, height, is_inference_frame, now))
            if not is_inference_frame:
                self._last_keyframe_time = now
            return True
        except queue.Full:
            self._dropped += 1
            return False

    def flush(self, timeout_seconds: float = 10.0) -> Dict[str, Any]:
        """Drain queue and wait for writer. Returns status dict."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and self._queue.qsize() > 0:
            time.sleep(0.1)
        remaining = self._queue.qsize()

        with self._lock:
            status = {
                "written": self._written, "dropped": self._dropped,
                "errors": self._errors, "queue_remaining": remaining,
                "frames_dir": str(self.frames_dir),
            }
        return status

    def close(self, timeout_seconds: float = 10.0) -> Dict[str, Any]:
        """Stop writer, flush, write index."""
        self._stop_event.set()
        status = self.flush(timeout_seconds)
        if self._thread:
            self._thread.join(timeout=timeout_seconds - 0.5)

        # Write index
        with self._lock:
            index_path = self.frames_dir / "index.jsonl"
            with open(index_path, "w") as f:
                import json
                for entry in self._index_entries:
                    f.write(json.dumps(entry) + "\n")
            status["index_entries"] = len(self._index_entries)

        return status
