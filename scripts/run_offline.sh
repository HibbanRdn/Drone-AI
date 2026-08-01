#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG="${GAP_PLOT_AI_CONFIG:-${APP_ROOT}/config/app.yaml}"
VIDEO="${1:?usage: run_offline.sh VIDEO [pt|onnx] [MAX_FRAMES]}"
BACKEND="${2:-pt}"
MAX_FRAMES="${3:-3}"

"${APP_ROOT}/scripts/check_disk_space.sh" \
  --path "${APP_ROOT}" --min-mib "${GAP_PLOT_AI_RUNTIME_MIN_FREE_MIB:-1536}" \
  --operation "offline inference"

exec "${APP_ROOT}/.venv/bin/gap-plot-ai-offline" \
  --config "${CONFIG}" \
  --video "${VIDEO}" \
  --backend "${BACKEND}" \
  --max-frames "${MAX_FRAMES}" \
  --snapshot-every 1 \
  --preview
