from __future__ import annotations

import importlib.util
from pathlib import Path


APP_ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "live_config_env", APP_ROOT / "scripts/live_config_env.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_native_liveview_environment_comes_from_validated_config() -> None:
    values = MODULE.environment(APP_ROOT / "config/live.yaml")
    assert values == {
        "GAP_PLOT_AI_LIVEVIEW_INPUT_MODE": "decoded_rgb",
        "GAP_PLOT_AI_STREAM_TIMEOUT_MS": "5000",
        "GAP_PLOT_AI_RECONNECT_INTERVAL_MS": "5000",
        "GAP_PLOT_AI_WORKER_HEARTBEAT_TIMEOUT_MS": "3000",
        "GAP_PLOT_AI_STALE_RESULT_TIMEOUT_MS": "1500",
    }
