#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_ROOT="${GAP_PLOT_AI_APP_ROOT:-${SOURCE_ROOT}}"

tail -n "${GAP_PLOT_AI_LOG_LINES:-100}" \
  "${APP_ROOT}/runtime/logs/native.log" \
  "${APP_ROOT}/runtime/logs/worker.log" 2>/dev/null
