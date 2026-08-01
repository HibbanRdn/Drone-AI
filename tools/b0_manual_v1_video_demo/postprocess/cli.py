"""Command-line interface for Gap Plot post-processing."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Sequence

from .pipeline import prepare, run_gap_analysis
from .plots import run_plot_editor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gap Plot B0 Manual-v1 post-processing")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare", help="audit, register, mosaic, and fuse unique plants")
    prepare_parser.add_argument("--inference-run", type=Path, required=True)
    prepare_parser.add_argument("--output", type=Path)
    prepare_parser.add_argument("--config", type=Path, help="JSON overrides")
    prepare_parser.add_argument("--max-frames", type=int)
    prepare_parser.add_argument("--smoke-test", action="store_true")
    editor_parser = commands.add_parser("plot-editor", help="draw and save manual plot polygons")
    editor_parser.add_argument("--postprocess-run", type=Path, required=True)
    gap_parser = commands.add_parser("analyze-gaps", help="reconstruct rows and find internal covered gaps")
    gap_parser.add_argument("--postprocess-run", type=Path, required=True)
    gap_parser.add_argument("--plots", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.command == "prepare":
        overrides = json.loads(args.config.read_text(encoding="utf-8")) if args.config else None
        output = prepare(args.inference_run, args.output, overrides, args.max_frames, args.smoke_test)
        print(output)
    elif args.command == "plot-editor":
        run_plot_editor(args.postprocess_run)
    elif args.command == "analyze-gaps":
        report = run_gap_analysis(args.postprocess_run, args.plots)
        print(json.dumps(report, indent=2))
    return 0
