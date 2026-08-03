#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_PYTHON="python3"

if [[ "$(uname -s)" == "Linux" && "$(uname -m)" == "aarch64" ]]; then
  echo "bootstrap_dev.sh hanya untuk host development, bukan Manifold 3." >&2
  echo "Audit dependency target Python 3.8/JetPack terlebih dahulu." >&2
  exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-${DEFAULT_PYTHON}}"

if [[ ! -x "$(command -v "${PYTHON_BIN}")" ]]; then
  echo "Python tidak ditemukan: ${PYTHON_BIN}" >&2
  exit 1
fi

"${PYTHON_BIN}" -c \
  'import sys; assert (3, 10) <= sys.version_info[:2] <= (3, 12), "Python 3.10..3.12 required"'

"${PYTHON_BIN}" -m venv "${APP_ROOT}/.venv"
"${APP_ROOT}/.venv/bin/python" -m pip install --upgrade pip
"${APP_ROOT}/.venv/bin/python" -m pip install -e "${APP_ROOT}[host,export,test,postprocess]"

"${APP_ROOT}/.venv/bin/python" -c \
  "from gap_plot_ai.config import load_config; load_config('${APP_ROOT}/config/app.yaml'); print('config_ok')"
echo "Venv siap: ${APP_ROOT}/.venv"
echo "Template PSDK tersedia; nilai lokal harus berada di config/dji_sdk_app_info.local.h."
