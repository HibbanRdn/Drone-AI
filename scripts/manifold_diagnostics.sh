#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_ROOT="${1:-${APP_ROOT}/runtime/diagnostics}"
mkdir -p "${OUTPUT_ROOT}"

{
  echo "generated_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "kernel=$(uname -a)"
  echo "machine=$(uname -m)"
  echo "python=$(python3 --version 2>&1)"
  echo "cmake=$(cmake --version 2>&1 | head -n 1 || true)"
  echo "gcc=$(gcc --version 2>&1 | head -n 1 || true)"
  echo "cuda_home=${CUDA_HOME:-/usr/local/cuda-11.4}"
  echo "psdk_root=${PSDK_ROOT:-unset}"
} > "${OUTPUT_ROOT}/platform.txt"

if command -v tegrastats >/dev/null 2>&1; then
  timeout 5s tegrastats --interval 1000 > "${OUTPUT_ROOT}/tegrastats.txt" 2>&1 || true
fi
if command -v nvidia-smi >/dev/null 2>&1; then
  timeout 10s nvidia-smi -q > "${OUTPUT_ROOT}/nvidia-smi.txt" 2>&1 || true
fi
if command -v dpkg-query >/dev/null 2>&1; then
  dpkg-query -W \
    'cuda*' 'libnvinfer*' 'python3-libnvinfer*' 'libopencv*' \
    > "${OUTPUT_ROOT}/packages.txt" 2>&1 || true
fi

python3 "${APP_ROOT}/scripts/check_manifold_ai_runtime.py" --phase runtime \
  > "${OUTPUT_ROOT}/python_runtime.txt" 2>&1
bash "${APP_ROOT}/scripts/validate_engine_readonly.sh" \
  "${OUTPUT_ROOT}/tensorrt_engine.json"

if [[ -x "${APP_ROOT}/build/manifold/gap_plot_ai" ]]; then
  ldd "${APP_ROOT}/build/manifold/gap_plot_ai" \
    > "${OUTPUT_ROOT}/gap_plot_ai.ldd.txt"
  if grep -q 'not found' "${OUTPUT_ROOT}/gap_plot_ai.ldd.txt"; then
    echo "BLOCKED: unresolved native library dependencies" >&2
    exit 5
  fi
fi
echo "manifold_diagnostics_complete=true"
