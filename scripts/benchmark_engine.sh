#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_ROOT="${GAP_PLOT_AI_APP_ROOT:-${SOURCE_ROOT}}"
REPORT_DIR="${APP_ROOT}/runtime/reports"
PYTHON_BIN="${APP_ROOT}/.venv/bin/python"
CONFIG="${GAP_PLOT_AI_CONFIG:-${SOURCE_ROOT}/config/app.yaml}"

if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "Benchmark TensorRT hanya di Manifold 3." >&2
  exit 2
fi
if ! command -v trtexec >/dev/null 2>&1; then
  echo "trtexec tidak tersedia pada runtime yang diaudit." >&2
  exit 3
fi
if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python target belum tersedia: ${PYTHON_BIN}" >&2
  exit 3
fi
"${SOURCE_ROOT}/scripts/check_disk_space.sh" \
  --path "${APP_ROOT}" --min-mib "${GAP_PLOT_AI_REPORT_MIN_FREE_MIB:-512}" \
  --operation "TensorRT benchmark report"
mkdir -p "${REPORT_DIR}"

export GAP_PLOT_AI_APP_ROOT="${APP_ROOT}"
mapfile -t engines < <("${PYTHON_BIN}" - "${CONFIG}" <<'PY'
from pathlib import Path
import sys

from gap_plot_ai.config import load_config

config = load_config(sys.argv[1])
for name in ("detector", "segmenter"):
    engine = Path(config["models"][name]["engine_path"])
    print(engine.with_suffix(".raw.engine"))
PY
)
for engine in "${engines[@]}"; do
  if [[ ! -f "${engine}" ]]; then
    echo "Raw engine benchmark tidak ditemukan: ${engine}" >&2
    exit 3
  fi
  model="$(basename "${engine}" .raw.engine)"
  trtexec --loadEngine="${engine}" --warmUp=500 --duration=10 \
    > "${REPORT_DIR}/${model}_trtexec.log" 2>&1
done
echo "${REPORT_DIR}"
