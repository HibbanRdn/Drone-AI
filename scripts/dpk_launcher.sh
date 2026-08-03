#!/usr/bin/env bash
set -euo pipefail

BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${BIN_DIR}/.." && pwd)"
PAYLOAD_DIR="${APP_DIR}/payload"
PYTHON_BIN="${PAYLOAD_DIR}/python/bin/python3"
NATIVE_BIN="${PAYLOAD_DIR}/bin/gap_plot_ai_native"
CONFIG="${PAYLOAD_DIR}/share/config/live.yaml"
IPC_DIR="${GAP_PLOT_AI_IPC_DIR:-/dev/shm/ggp-drone-ai}"

if [[ ! -x "${PYTHON_BIN}" || ! -x "${NATIVE_BIN}" ]]; then
  echo "DPK runtime bundle tidak lengkap." >&2
  exit 3
fi

mkdir -p "${IPC_DIR}" "${APP_DIR}/data/logs" "${APP_DIR}/data/runtime"
export GAP_PLOT_AI_APP_ROOT="${PAYLOAD_DIR}"
export GAP_PLOT_AI_RUNTIME_ROOT="${APP_DIR}/data/runtime"
export GAP_PLOT_AI_IPC_DIR="${IPC_DIR}"
export GAP_PLOT_AI_WIDGET_DIR="${PAYLOAD_DIR}/share/config/widget"
export GAP_PLOT_AI_STALE_RESULT_TIMEOUT_MS="${GAP_PLOT_AI_STALE_RESULT_TIMEOUT_MS:-1500}"
export GAP_PLOT_AI_WORKER_HEARTBEAT_TIMEOUT_MS="${GAP_PLOT_AI_WORKER_HEARTBEAT_TIMEOUT_MS:-3000}"
export GAP_PLOT_AI_ENABLE_RENDERED_STREAM="${GAP_PLOT_AI_ENABLE_RENDERED_STREAM:-0}"

"${PYTHON_BIN}" -m gap_plot_ai.worker \
  --config "${CONFIG}" --ipc-dir "${IPC_DIR}" --backend engine \
  >> "${APP_DIR}/data/logs/worker.log" 2>&1 &
worker_pid=$!

cleanup() {
  if kill -0 "${worker_pid}" 2>/dev/null; then
    kill -TERM "${worker_pid}" 2>/dev/null || true
    wait "${worker_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

"${NATIVE_BIN}" >> "${APP_DIR}/data/logs/native.log" 2>&1
