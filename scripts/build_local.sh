#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_ROOT="$(cd "${APP_ROOT}/.." && pwd)"
PSDK_ROOT="${PSDK_ROOT:?Set PSDK_ROOT ke checkout resmi Payload-SDK tag 3.16.0}"

export PLOT_GAP_ROOT="${PROJECT_ROOT}"
export GAP_PLOT_AI_APP_ROOT="${APP_ROOT}"

"${APP_ROOT}/scripts/check_disk_space.sh" \
  --path "${APP_ROOT}" --min-mib "${GAP_PLOT_AI_BUILD_MIN_FREE_MIB:-512}" \
  --operation "local validation"

"${APP_ROOT}/.venv/bin/python" "${APP_ROOT}/scripts/verify_psdk_316.py" \
  "${PSDK_ROOT}"
"${APP_ROOT}/.venv/bin/python" -m compileall -q "${APP_ROOT}/src" "${APP_ROOT}/tests"
"${APP_ROOT}/.venv/bin/python" -m pytest "${APP_ROOT}/tests"

if command -v clang++ >/dev/null 2>&1; then
  clang++ -std=c++17 -fsyntax-only "${APP_ROOT}/src/psdk/main.cpp" \
    -I"${APP_ROOT}/include" \
    -I"${PSDK_ROOT}/psdk_lib/include" \
    -I"${PSDK_ROOT}/samples/sample_c++/platform/linux/common" \
    -I"${PSDK_ROOT}/samples/sample_c++/platform/linux/manifold3/hal"
  clang++ -std=c++17 -fsyntax-only "${APP_ROOT}/src/psdk/main.cpp" \
    -DGAP_PLOT_AI_COMPILED_APP_INFO=1 \
    -I"${APP_ROOT}/include" \
    -I"${APP_ROOT}/tests/fixtures" \
    -I"${PSDK_ROOT}/psdk_lib/include" \
    -I"${PSDK_ROOT}/samples/sample_c++/platform/linux/common" \
    -I"${PSDK_ROOT}/samples/sample_c++/platform/linux/manifold3/hal"
fi
echo "Local build/test checks passed."
