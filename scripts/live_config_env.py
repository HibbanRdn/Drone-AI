#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict

from gap_plot_ai.config import load_config


def environment(config_path: Path) -> Dict[str, str]:
    config = load_config(config_path)
    live = config["live"]
    overlay = config["overlay"]
    return {
        "GAP_PLOT_AI_LIVEVIEW_INPUT_MODE": str(live["input_mode"]),
        "GAP_PLOT_AI_STREAM_TIMEOUT_MS": str(int(live["stream_timeout_ms"])),
        "GAP_PLOT_AI_RECONNECT_INTERVAL_MS": str(
            int(live["reconnect_interval_ms"])
        ),
        "GAP_PLOT_AI_WORKER_HEARTBEAT_TIMEOUT_MS": str(
            int(live["worker_heartbeat_timeout_ms"])
        ),
        "GAP_PLOT_AI_STALE_RESULT_TIMEOUT_MS": str(
            int(overlay["stale_result_timeout_ms"])
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Emit validated non-secret native Liveview environment"
    )
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    for name, value in environment(args.config.resolve()).items():
        print(f"{name}={value}")


if __name__ == "__main__":
    main()
