#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_ROOT="${GAP_PLOT_AI_APP_ROOT:-${SOURCE_ROOT}}"
PSDK_ROOT="${PSDK_ROOT:?Set PSDK_ROOT ke Payload-SDK 3.16.0 pada Manifold}"
BUILD_DIR="${APP_ROOT}/build"
SECRETS="${GAP_PLOT_AI_SECRETS_FILE:-${APP_ROOT}/config/secrets.env}"
GENERATED_HEADER="${APP_ROOT}/runtime/generated/dji_sdk_app_info.generated.h"
PYTHON_BIN="${GAP_PLOT_AI_CREDENTIAL_PYTHON:-python3}"

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "aarch64" ]]; then
  echo "Build PSDK hanya di Linux aarch64 Manifold 3." >&2
  exit 2
fi
"${PYTHON_BIN}" "${SOURCE_ROOT}/scripts/verify_psdk_316.py" "${PSDK_ROOT}"

"${PYTHON_BIN}" "${SOURCE_ROOT}/scripts/psdk_credentials.py" validate \
  --secrets "${SECRETS}" --require-runtime
"${PYTHON_BIN}" "${SOURCE_ROOT}/scripts/psdk_credentials.py" generate \
  --secrets "${SECRETS}" --output "${GENERATED_HEADER}" --require-runtime

cmake -S "${SOURCE_ROOT}" -B "${BUILD_DIR}" \
  -DPSDK_ROOT="${PSDK_ROOT}" \
  -DGAP_PLOT_AI_APP_INFO_HEADER="${GENERATED_HEADER}" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build "${BUILD_DIR}" --parallel 2
echo "${BUILD_DIR}/bin/gap_plot_ai"
