#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_ROOT="${GAP_PLOT_AI_APP_ROOT:-${SOURCE_ROOT}}"
CONFIG="${GAP_PLOT_AI_CONFIG:-${SOURCE_ROOT}/config/app.yaml}"
IPC_DIR="${APP_ROOT}/runtime/ipc"
PID_DIR="${APP_ROOT}/runtime/pids"
GATE="${APP_ROOT}/runtime/gates/psdk_liveview_verified"
NATIVE_BIN="${APP_ROOT}/build/bin/gap_plot_ai"
PYTHON_BIN="${APP_ROOT}/.venv/bin/python"

if [[ ! -f "${GATE}" ]]; then
  echo "Blocked: official PSDK sample/liveview/widget gate belum lulus." >&2
  exit 4
fi
if [[ ! -x "${NATIVE_BIN}" || ! -x "${PYTHON_BIN}" ]]; then
  echo "Build binary/venv Manifold belum siap." >&2
  exit 3
fi
"${SOURCE_ROOT}/scripts/check_disk_space.sh" \
  --path "${APP_ROOT}" --min-mib "${GAP_PLOT_AI_RUNTIME_MIN_FREE_MIB:-1536}" \
  --operation "Gap Plot AI runtime"

mkdir -p "${IPC_DIR}" "${PID_DIR}" "${APP_ROOT}/runtime/logs"
export GAP_PLOT_AI_APP_ROOT="${APP_ROOT}"
export PLOT_GAP_ROOT="${PLOT_GAP_ROOT:-${APP_ROOT}}"
export GAP_PLOT_AI_IPC_DIR="${IPC_DIR}"
export GAP_PLOT_AI_WIDGET_DIR="${SOURCE_ROOT}/config/widget"

"${PYTHON_BIN}" -m gap_plot_ai.worker \
  --config "${CONFIG}" --ipc-dir "${IPC_DIR}" --backend auto \
  >> "${APP_ROOT}/runtime/logs/worker.log" 2>&1 &
worker_pid=$!
echo "${worker_pid}" > "${PID_DIR}/worker.pid"

cleanup_worker() {
  if kill -0 "${worker_pid}" 2>/dev/null; then
    kill -TERM "${worker_pid}" 2>/dev/null || true
    wait "${worker_pid}" 2>/dev/null || true
  fi
}
trap cleanup_worker EXIT INT TERM

for _ in $(seq 1 100); do
  if [[ -f "${IPC_DIR}/worker_status.json" ]]; then
    break
  fi
  if ! kill -0 "${worker_pid}" 2>/dev/null; then
    echo "Worker berhenti saat initialization." >&2
    exit 1
  fi
  sleep 0.1
done

echo "$$" > "${PID_DIR}/native.pid"
"${NATIVE_BIN}" >> "${APP_ROOT}/runtime/logs/native.log" 2>&1
