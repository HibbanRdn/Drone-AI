#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${APP_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Development venv belum tersedia. Jalankan scripts/bootstrap_dev.sh." >&2
  exit 2
fi

RUNTIME_ROOT="${GAP_PLOT_AI_REPLAY_RUNTIME_ROOT:-${APP_ROOT}/runtime/replay}"
if [[ $# -gt 0 ]]; then
  exec "${PYTHON_BIN}" -m gap_plot_ai.live \
    --config "${APP_ROOT}/config/live.yaml" --video "$1" \
    --runtime-root "${RUNTIME_ROOT}"
fi

exec "${PYTHON_BIN}" -m gap_plot_ai.live \
  --config "${APP_ROOT}/config/live.yaml" --synthetic-frames 12 \
  --width 1920 --height 1080 --runtime-root "${RUNTIME_ROOT}"
