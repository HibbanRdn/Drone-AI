#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_ROOT="${GAP_PLOT_AI_APP_ROOT:-${SOURCE_ROOT}}"
REPORT_DIR="${APP_ROOT}/runtime/reports"

if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "Benchmark TensorRT hanya di Manifold 3." >&2
  exit 2
fi
if ! command -v trtexec >/dev/null 2>&1; then
  echo "trtexec tidak tersedia pada runtime yang diaudit." >&2
  exit 3
fi
mkdir -p "${REPORT_DIR}"

for model in plant_detector_b0_manual_v1_best plot_segmenter_b4_selected_best; do
  engine="${APP_ROOT}/runtime/models/${model}.raw.engine"
  if [[ ! -f "${engine}" ]]; then
    echo "Raw engine benchmark tidak ditemukan: ${engine}" >&2
    exit 3
  fi
  trtexec --loadEngine="${engine}" --warmUp=500 --duration=10 \
    > "${REPORT_DIR}/${model}_trtexec.log" 2>&1
done
echo "${REPORT_DIR}"
