#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_ROOT="${GAP_PLOT_AI_APP_ROOT:-${SOURCE_ROOT}}"
PSDK_ROOT="${PSDK_ROOT:?Set PSDK_ROOT ke Payload-SDK 3.16.0 pada Manifold}"
BUILD_DIR="${APP_ROOT}/build"
APP_INFO_HEADER="${GAP_PLOT_AI_APP_INFO_HEADER:-${SOURCE_ROOT}/config/dji_sdk_app_info.h}"
PYTHON_BIN="${GAP_PLOT_AI_PYTHON:-python3}"

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "aarch64" ]]; then
  echo "Build PSDK hanya di Linux aarch64 Manifold 3." >&2
  exit 2
fi
"${SOURCE_ROOT}/scripts/check_disk_space.sh" \
  --path "${APP_ROOT}" --min-mib "${GAP_PLOT_AI_BUILD_MIN_FREE_MIB:-1024}" \
  --operation "Manifold CMake build"
"${PYTHON_BIN}" "${SOURCE_ROOT}/scripts/verify_psdk_316.py" "${PSDK_ROOT}"
if [[ ! -f "${APP_INFO_HEADER}" ]]; then
  echo "PSDK app identity header tidak ditemukan: ${APP_INFO_HEADER}" >&2
  exit 3
fi

cmake -S "${SOURCE_ROOT}" -B "${BUILD_DIR}" \
  -DPSDK_ROOT="${PSDK_ROOT}" \
  -DGAP_PLOT_AI_APP_INFO_HEADER="${APP_INFO_HEADER}" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build "${BUILD_DIR}" --parallel 2
echo "${BUILD_DIR}/bin/gap_plot_ai"
