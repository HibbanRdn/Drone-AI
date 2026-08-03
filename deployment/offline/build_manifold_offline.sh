#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${GAP_PLOT_AI_INSTALL_ROOT:-/home/dji/gap_plot_ai_dev}"
VENDOR_ROOT="${GAP_PLOT_AI_VENDOR_ROOT:-/home/dji/vendor}"
APP_COMMIT="@APP_COMMIT@"
PSDK_COMMIT="@PSDK_COMMIT@"
APP_SOURCE="${INSTALL_ROOT}/releases/${APP_COMMIT}/source"
APP_RELEASE="${INSTALL_ROOT}/releases/${APP_COMMIT}"
PSDK_ROOT="${VENDOR_ROOT}/Payload-SDK-@PSDK_TAG@-${PSDK_COMMIT:0:12}"
VENV="${APP_SOURCE}/.venv"
WHEEL_DIR="${APP_RELEASE}/offline_wheels"
REQUIREMENTS="${APP_SOURCE}/requirements-manifold.txt"

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
export GAP_PLOT_AI_PYTHON="${GAP_PLOT_AI_PYTHON:-python3}"
export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda-11.4}"
export CUDACXX="${CUDACXX:-/usr/local/cuda/bin/nvcc}"
export PATH="/usr/local/cuda/bin:${PATH}"
export LD_LIBRARY_PATH="/usr/local/cuda-11.4/lib64:${LD_LIBRARY_PATH:-}"

python3 "${APP_SOURCE}/scripts/verify_psdk_316.py" "${PSDK_ROOT}"
if [[ ! -x "${VENV}/bin/python" ]]; then
  python3 -m venv --system-site-packages "${VENV}"
fi
if grep -Eq '^[[:space:]]*[^#[:space:]]' "${REQUIREMENTS}"; then
  if [[ ! -d "${WHEEL_DIR}" ]]; then
    echo "Offline wheels required by requirements-manifold.txt are missing." >&2
    exit 4
  fi
  "${VENV}/bin/python" -m pip install --no-index --no-deps \
    --find-links "${WHEEL_DIR}" -r "${REQUIREMENTS}"
fi
export GAP_PLOT_AI_PYTHON="${VENV}/bin/python"
export PYTHONPATH="${APP_SOURCE}/src${PYTHONPATH:+:${PYTHONPATH}}"
"${VENV}/bin/python" -c \
  'import gap_plot_ai; print("gap_plot_ai_source_import=true")'
"${APP_SOURCE}/scripts/build_psdk_sample.sh"
"${APP_SOURCE}/scripts/build_manifold.sh"

LDD_REPORT="${APP_SOURCE}/runtime/reports/ldd_gap_plot_ai.txt"
mkdir -p "$(dirname "${LDD_REPORT}")"
ldd "${APP_SOURCE}/build/bin/gap_plot_ai" | tee "${LDD_REPORT}"
if grep -q 'not found' "${LDD_REPORT}"; then
  echo "Native binary has unresolved shared libraries." >&2
  exit 5
fi

echo "build_complete=${APP_SOURCE}/build/bin/gap_plot_ai"
echo "TensorRT runtime and aircraft Liveview were not exercised by this build."
