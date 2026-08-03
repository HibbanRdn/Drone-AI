#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_ROOT="${GAP_PLOT_AI_APP_ROOT:-${SOURCE_ROOT}}"
PSDK_ROOT="${PSDK_ROOT:?Set PSDK_ROOT ke Payload-SDK 3.16.0 pada Manifold}"
BUILD_DIR="${PSDK_SAMPLE_BUILD_DIR:-${APP_ROOT}/build/official_psdk_sample_3.16.0}"
SECRETS="${GAP_PLOT_AI_SECRETS_FILE:-${APP_ROOT}/config/secrets.env}"
GENERATED_HEADER="${APP_ROOT}/runtime/generated/dji_sdk_app_info.generated.h"
PYTHON_BIN="${GAP_PLOT_AI_CREDENTIAL_PYTHON:-python3}"
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
"${PYTHON_BIN}" "${SOURCE_ROOT}/scripts/psdk_credentials.py" validate \
  --secrets "${SECRETS}" --require-runtime
"${PYTHON_BIN}" "${SOURCE_ROOT}/scripts/psdk_credentials.py" generate \
  --secrets "${SECRETS}" --output "${GENERATED_HEADER}" --require-runtime

# The upstream sample accepts credentials through CMAKE_CXX_FLAGS. Stage the
# private header under /tmp so a workspace path containing spaces is not split
# by the upstream CMake command line. Nothing is written into the DJI checkout.
PRIVATE_HEADER_STAGE="$(mktemp -d /tmp/gap_plot_ai_psdk_credentials.XXXXXX)"
STAGED_HEADER="${PRIVATE_HEADER_STAGE}/dji_sdk_app_info.generated.h"
install -m 0600 "${GENERATED_HEADER}" "${STAGED_HEADER}"

cmake -S "${PSDK_ROOT}" -B "${BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE=Release \
  "-DCMAKE_CXX_FLAGS:STRING=-include ${STAGED_HEADER}"
cmake --build "${BUILD_DIR}" --target dji_sdk_demo_on_manifold3_cxx --parallel 2
echo "${BUILD_DIR}/bin/dji_sdk_demo_on_manifold3_cxx"
