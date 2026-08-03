#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_ROOT="${GAP_PLOT_AI_APP_ROOT:-${SOURCE_ROOT}}"
NATIVE_BIN="${APP_ROOT}/build/bin/gap_plot_ai"
GATE="${APP_ROOT}/runtime/gates/psdk_liveview_verified"
IPC_DIR="${GAP_PLOT_AI_IPC_DIR:-/dev/shm/ggp-drone-ai}"

if [[ ! -f "${GATE}" || ! -x "${NATIVE_BIN}" ]]; then
  echo "Official PSDK gate atau native binary belum tersedia." >&2
  exit 3
fi

mkdir -p "${IPC_DIR}" "${APP_ROOT}/runtime/logs" "${APP_ROOT}/data/logs"
export GAP_PLOT_AI_APP_ROOT="${APP_ROOT}"
export GAP_PLOT_AI_IPC_DIR="${IPC_DIR}"
export GAP_PLOT_AI_WIDGET_DIR="${SOURCE_ROOT}/config/widget"
export GAP_PLOT_AI_STATIC_OVERLAY_DEBUG=1
export GAP_PLOT_AI_ENABLE_RENDERED_STREAM=0

exec "${NATIVE_BIN}"
