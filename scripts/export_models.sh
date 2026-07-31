#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_ROOT="$(cd "${APP_ROOT}/.." && pwd)"
CONFIG="${1:-${APP_ROOT}/config/app.yaml}"

"${APP_ROOT}/scripts/check_disk_space.sh" \
  --path "${APP_ROOT}" --min-mib "${GAP_PLOT_AI_EXPORT_MIN_FREE_MIB:-1024}" \
  --operation "ONNX model export"

export PLOT_GAP_ROOT="${PROJECT_ROOT}"
export GAP_PLOT_AI_APP_ROOT="${APP_ROOT}"
exec "${APP_ROOT}/.venv/bin/gap-plot-ai-export" --config "${CONFIG}"
