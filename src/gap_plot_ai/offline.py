from __future__ import annotations

import argparse
import signal
from pathlib import Path
from typing import Optional

import cv2

from .config import load_config
from .runtime import InferenceRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gap Plot AI offline integration runner")
    parser.add_argument("--config", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--backend", choices=("auto", "pt", "onnx", "engine"), default="pt")
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--max-frames", type=int, default=10)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--snapshot-every", type=int, default=0)
    parser.add_argument("--preview", action="store_true")
    return parser


def run(args: argparse.Namespace) -> Path:
    if args.start_frame < 0 or args.max_frames < 1 or args.stride < 1:
        raise ValueError("start-frame >= 0, max-frames >= 1, stride >= 1")
    config = load_config(args.config)
    video_path = Path(args.video).expanduser().resolve()
    if not video_path.is_file():
        raise FileNotFoundError(video_path)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise OSError(f"Video tidak dapat dibuka: {video_path}")
    capture.set(cv2.CAP_PROP_POS_FRAMES, args.start_frame)
    runtime = InferenceRuntime(config, backend=args.backend)
    interrupted = False

    def _stop(_signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True

    previous_sigint = signal.signal(signal.SIGINT, _stop)
    previous_sigterm = signal.signal(signal.SIGTERM, _stop)
    preview_writer: Optional[cv2.VideoWriter] = None
    processed = 0
    source_index = args.start_frame
    try:
        while processed < args.max_frames and not interrupted:
            ok, frame = capture.read()
            if not ok:
                break
            if (source_index - args.start_frame) % args.stride != 0:
                source_index += 1
                continue
            offset_ms = capture.get(cv2.CAP_PROP_POS_MSEC)
            result, overlay = runtime.process_frame(
                frame,
                frame_index=source_index,
                capture_timestamp=f"video_offset_seconds:{offset_ms / 1000.0:.6f}",
                camera_source="offline_wide_video",
                snapshot=bool(
                    args.snapshot_every > 0 and processed % args.snapshot_every == 0
                ),
            )
            if args.preview:
                if preview_writer is None:
                    fps = capture.get(cv2.CAP_PROP_FPS) or 1.0
                    preview_path = runtime.writer.session_dir / "offline_preview.mp4"
                    preview_writer = cv2.VideoWriter(
                        str(preview_path),
                        cv2.VideoWriter_fourcc(*"mp4v"),
                        max(0.1, fps / args.stride),
                        (result.image_width, result.image_height),
                    )
                    if not preview_writer.isOpened():
                        raise OSError(f"Preview writer gagal: {preview_path}")
                preview_writer.write(overlay)
            processed += 1
            source_index += 1
        status = "interrupted" if interrupted else "completed"
        return runtime.close(status)
    except Exception:
        runtime.writer.logger.exception("offline_run_failed")
        runtime.close("error")
        raise
    finally:
        if preview_writer is not None:
            preview_writer.release()
        capture.release()
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)


def main() -> None:
    args = build_parser().parse_args()
    summary = run(args)
    print(summary)


if __name__ == "__main__":
    main()
