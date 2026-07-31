#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_ROOT="${GAP_PLOT_AI_APP_ROOT:-${SOURCE_ROOT}}"
PID_DIR="${APP_ROOT}/runtime/pids"
STATUS="${APP_ROOT}/runtime/ipc/worker_status.json"

for component in native worker; do
  pidfile="${PID_DIR}/${component}.pid"
  state="stopped"
  if [[ -f "${pidfile}" ]]; then
    pid="$(tr -cd '0-9' < "${pidfile}")"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      state="running(pid=${pid})"
    fi
  fi
  echo "${component}: ${state}"
done
if [[ -f "${STATUS}" ]]; then
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("ai:",d.get("status"),"frame:",d.get("frame_index"),"fps:",d.get("fps"),"latency_ms:",d.get("latency_ms"))' "${STATUS}"
fi
