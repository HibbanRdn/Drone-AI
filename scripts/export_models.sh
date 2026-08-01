#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG="${1:-${APP_ROOT}/config/app.yaml}"

"${APP_ROOT}/scripts/check_disk_space.sh" \
  --path "${APP_ROOT}" --min-mib "${GAP_PLOT_AI_EXPORT_MIN_FREE_MIB:-1024}" \
  --operation "ONNX model export"

exec "${APP_ROOT}/.venv/bin/gap-plot-ai-export" --config "${CONFIG}"
