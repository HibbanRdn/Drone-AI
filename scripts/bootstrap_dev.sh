#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_ROOT="$(cd "${APP_ROOT}/.." && pwd)"
DEFAULT_PYTHON="${PROJECT_ROOT}/local_inference/b0_manual_v1_video_demo/.venv/bin/python"

if [[ "$(uname -s)" == "Linux" && "$(uname -m)" == "aarch64" ]]; then
  echo "bootstrap_dev.sh hanya untuk host development, bukan Manifold 3." >&2
  echo "Audit dependency target Python 3.8/JetPack terlebih dahulu." >&2
  exit 2
fi

if [[ -x "${DEFAULT_PYTHON}" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-${DEFAULT_PYTHON}}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

if [[ ! -x "$(command -v "${PYTHON_BIN}")" ]]; then
  echo "Python tidak ditemukan: ${PYTHON_BIN}" >&2
  exit 1
fi

"${PYTHON_BIN}" -c \
  'import sys; assert (3, 10) <= sys.version_info[:2] <= (3, 12), "Python 3.10..3.12 required"'

"${PYTHON_BIN}" -m venv "${APP_ROOT}/.venv"
"${APP_ROOT}/.venv/bin/python" -m pip install --upgrade pip
"${APP_ROOT}/.venv/bin/python" -m pip install -e "${APP_ROOT}[host,export,test]"

if [[ ! -f "${APP_ROOT}/config/app.yaml" ]]; then
  cp "${APP_ROOT}/config/app.example.yaml" "${APP_ROOT}/config/app.yaml"
fi

export PLOT_GAP_ROOT="${PROJECT_ROOT}"
export GAP_PLOT_AI_APP_ROOT="${APP_ROOT}"
"${APP_ROOT}/.venv/bin/python" -c \
  "from gap_plot_ai.config import load_config; load_config('${APP_ROOT}/config/app.yaml'); print('config_ok')"
echo "Venv siap: ${APP_ROOT}/.venv"
echo "Secret tidak dibuat. Gunakan config/secrets.env hanya pada mesin/perangkat yang sesuai."
