#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
GATE_DIR="${GAP_PLOT_AI_APP_ROOT:-${APP_ROOT}}/runtime/gates"

if [[ "${1:-}" != "--official-sample-liveview-and-widget-passed" ]]; then
  echo "Gate tidak dibuat." >&2
  echo "Jalankan hanya setelah sample resmi membaca M4E, telemetry, M4E_VIS frame, dan widget Pilot 2." >&2
  echo "usage: mark_psdk_verified.sh --official-sample-liveview-and-widget-passed" >&2
  exit 2
fi
mkdir -p "${GATE_DIR}"
date -u '+%Y-%m-%dT%H:%M:%SZ' > "${GATE_DIR}/psdk_liveview_verified"
echo "${GATE_DIR}/psdk_liveview_verified"
