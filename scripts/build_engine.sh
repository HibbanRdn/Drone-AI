#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_ROOT="${GAP_PLOT_AI_APP_ROOT:-${SOURCE_ROOT}}"
CONFIG="${GAP_PLOT_AI_CONFIG:-${SOURCE_ROOT}/config/app.yaml}"

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "aarch64" ]]; then
  echo "TensorRT engine hanya boleh dibangun pada Manifold 3." >&2
  exit 2
fi
if [[ ! -x "${APP_ROOT}/.venv/bin/python" ]]; then
  echo "Venv Manifold belum tersedia; audit runtime/dependency sebelum instalasi." >&2
  exit 3
fi
"${APP_ROOT}/.venv/bin/python" \
  "${SOURCE_ROOT}/scripts/check_manifold_ai_runtime.py" --phase engine
"${SOURCE_ROOT}/scripts/check_disk_space.sh" \
  --path "${APP_ROOT}" --min-mib "${GAP_PLOT_AI_ENGINE_MIN_FREE_MIB:-1024}" \
  --operation "TensorRT engine build"

export GAP_PLOT_AI_APP_ROOT="${APP_ROOT}"
export PLOT_GAP_ROOT="${PLOT_GAP_ROOT:-${APP_ROOT}}"
exec "${APP_ROOT}/.venv/bin/python" -m gap_plot_ai.build_engine \
  --config "${CONFIG}" --workspace-gib "${GAP_PLOT_AI_TRT_WORKSPACE_GIB:-2}"
