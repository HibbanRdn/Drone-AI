#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_ROOT="$(cd "${APP_ROOT}/.." && pwd)"
VIDEO="${1:?usage: parity.sh VIDEO onnx|engine [FRAME ...]}"
CANDIDATE="${2:?candidate backend wajib onnx atau engine}"
shift 2
if [[ "$#" -eq 0 ]]; then
  FRAMES=(0)
else
  FRAMES=("$@")
fi

"${APP_ROOT}/scripts/check_disk_space.sh" \
  --path "${APP_ROOT}" --min-mib "${GAP_PLOT_AI_REPORT_MIN_FREE_MIB:-512}" \
  --operation "model parity report"

export PLOT_GAP_ROOT="${PROJECT_ROOT}"
export GAP_PLOT_AI_APP_ROOT="${APP_ROOT}"
exec "${APP_ROOT}/.venv/bin/gap-plot-ai-parity" \
  --config "${GAP_PLOT_AI_CONFIG:-${APP_ROOT}/config/app.yaml}" \
  --video "${VIDEO}" \
  --candidate-backend "${CANDIDATE}" \
  --frames "${FRAMES[@]}"
