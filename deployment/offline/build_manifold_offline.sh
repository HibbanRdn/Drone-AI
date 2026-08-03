#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${GAP_PLOT_AI_INSTALL_ROOT:-/home/dji/gap_plot_ai_dev}"
VENDOR_ROOT="${GAP_PLOT_AI_VENDOR_ROOT:-/home/dji/vendor}"
APP_COMMIT="@APP_COMMIT@"
PSDK_COMMIT="@PSDK_COMMIT@"
APP_SOURCE="${INSTALL_ROOT}/releases/${APP_COMMIT}/source"
PSDK_ROOT="${VENDOR_ROOT}/Payload-SDK-@PSDK_TAG@-${PSDK_COMMIT:0:12}"

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "aarch64" ]]; then
  echo "Offline Manifold build requires Linux aarch64." >&2
  exit 2
fi
if [[ ! -d "${APP_SOURCE}" || ! -d "${PSDK_ROOT}" ]]; then
  echo "Run install_offline.sh before building." >&2
  exit 3
fi

export PIP_NO_INDEX=1
export GIT_TERMINAL_PROMPT=0
export PSDK_ROOT
export GAP_PLOT_AI_APP_ROOT="${APP_SOURCE}"
export GAP_PLOT_AI_CREDENTIAL_PYTHON="${GAP_PLOT_AI_CREDENTIAL_PYTHON:-python3}"
export GAP_PLOT_AI_SECRETS_FILE="${GAP_PLOT_AI_SECRETS_FILE:-${INSTALL_ROOT}/config/secrets.env}"

python3 "${APP_SOURCE}/scripts/verify_psdk_316.py" "${PSDK_ROOT}"
"${APP_SOURCE}/scripts/build_psdk_sample.sh"
"${APP_SOURCE}/scripts/build_manifold.sh"

echo "build_complete=${APP_SOURCE}/build/bin/gap_plot_ai"
echo "TensorRT runtime and aircraft Liveview were not exercised by this build."
