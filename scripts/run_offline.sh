#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_ROOT="$(cd "${APP_ROOT}/.." && pwd)"
CONFIG="${GAP_PLOT_AI_CONFIG:-${APP_ROOT}/config/app.yaml}"
VIDEO="${1:?usage: run_offline.sh VIDEO [pt|onnx] [MAX_FRAMES]}"
BACKEND="${2:-pt}"
MAX_FRAMES="${3:-3}"

export PLOT_GAP_ROOT="${PROJECT_ROOT}"
export GAP_PLOT_AI_APP_ROOT="${APP_ROOT}"
exec "${APP_ROOT}/.venv/bin/gap-plot-ai-offline" \
  --config "${CONFIG}" \
  --video "${VIDEO}" \
  --backend "${BACKEND}" \
  --max-frames "${MAX_FRAMES}" \
  --snapshot-every 1 \
  --preview
