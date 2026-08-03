#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_ROOT="${GAP_PLOT_AI_APP_ROOT:-${SOURCE_ROOT}}"
PSDK_ROOT="${PSDK_ROOT:?Set PSDK_ROOT ke Payload-SDK 3.16.0 pada Manifold}"
BUILD_DIR="${PSDK_SAMPLE_BUILD_DIR:-${APP_ROOT}/build/official_psdk_sample_3.16.0}"
APP_INFO_HEADER="${GAP_PLOT_AI_APP_INFO_HEADER:-${SOURCE_ROOT}/config/dji_sdk_app_info.local.h}"
PYTHON_BIN="${GAP_PLOT_AI_PYTHON:-python3}"
PRIVATE_HEADER_STAGE=""

cleanup_private_header() {
  if [[ -n "${PRIVATE_HEADER_STAGE}" && -d "${PRIVATE_HEADER_STAGE}" ]]; then
    rm -rf -- "${PRIVATE_HEADER_STAGE}"
  fi
}
trap cleanup_private_header EXIT INT TERM

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "aarch64" ]]; then
  echo "Official Manifold sample hanya dibangun di Linux aarch64." >&2
  exit 2
fi
"${PYTHON_BIN}" "${SOURCE_ROOT}/scripts/verify_psdk_316.py" "${PSDK_ROOT}"
if [[ ! -f "${APP_INFO_HEADER}" ]]; then
  echo "PSDK app identity header tidak ditemukan: ${APP_INFO_HEADER}" >&2
  exit 3
fi
"${PYTHON_BIN}" "${SOURCE_ROOT}/scripts/psdk_app_info.py" "${APP_INFO_HEADER}"

# The upstream sample accepts app identity through CMAKE_CXX_FLAGS. Stage the
# local ignored header under /tmp so a workspace path containing spaces is not split
# by the upstream CMake command line. Nothing is written into the DJI checkout.
PRIVATE_HEADER_STAGE="$(mktemp -d /tmp/gap_plot_ai_psdk_credentials.XXXXXX)"
STAGED_HEADER="${PRIVATE_HEADER_STAGE}/dji_sdk_app_info.generated.h"
install -m 0600 "${APP_INFO_HEADER}" "${STAGED_HEADER}"

cmake -S "${PSDK_ROOT}" -B "${BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE=Release \
  "-DCMAKE_CXX_FLAGS:STRING=-include ${STAGED_HEADER}"
cmake --build "${BUILD_DIR}" --target dji_sdk_demo_on_manifold3_cxx --parallel 2
echo "${BUILD_DIR}/bin/dji_sdk_demo_on_manifold3_cxx"
