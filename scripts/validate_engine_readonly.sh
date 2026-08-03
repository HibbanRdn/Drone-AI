#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE_DIR="${GAP_PLOT_AI_ENGINE_DIR:-/home/dji/gap_plot_ai_assets/models/engine}"
ENGINE_PATH="${GAP_PLOT_AI_DETECTOR_ENGINE_PATH:-}"
REPORT_PATH="${1:-${APP_ROOT}/runtime/diagnostics/tensorrt_engine.json}"

if [[ ! -d "${ENGINE_DIR}" ]]; then
  echo "BLOCKED: protected engine directory not found: ${ENGINE_DIR}" >&2
  exit 3
fi
ENGINE_DIR="$(readlink -f -- "${ENGINE_DIR}")"
if [[ -z "${ENGINE_PATH}" ]]; then
  shopt -s nullglob
  engine_candidates=("${ENGINE_DIR}"/*.engine)
  shopt -u nullglob
  if [[ "${#engine_candidates[@]}" -ne 1 ]]; then
    echo "BLOCKED: set GAP_PLOT_AI_DETECTOR_ENGINE_PATH; expected exactly one protected engine, found ${#engine_candidates[@]}" >&2
    exit 3
  fi
  ENGINE_PATH="${engine_candidates[0]}"
fi
ENGINE_PATH="$(readlink -f -- "${ENGINE_PATH}")"

case "${ENGINE_PATH}" in
  "${ENGINE_DIR}"/*) ;;
  *)
    echo "BLOCKED: engine must remain under /home/dji/gap_plot_ai_assets/models/engine" >&2
    exit 3
    ;;
esac

if [[ ! -f "${ENGINE_PATH}" ]]; then
  echo "BLOCKED: protected TensorRT engine not found: ${ENGINE_PATH}" >&2
  exit 3
fi

before_hash="$(sha256sum "${ENGINE_PATH}" | awk '{print $1}')"
before_size="$(stat -c '%s' "${ENGINE_PATH}")"
mkdir -p "$(dirname "${REPORT_PATH}")"
python3 "${APP_ROOT}/scripts/inspect_tensorrt_engine.py" \
  "${ENGINE_PATH}" --output "${REPORT_PATH}"

if command -v trtexec >/dev/null 2>&1; then
  timeout 60s trtexec \
    --loadEngine="${ENGINE_PATH}" \
    --skipInference \
    --verbose 2>&1 | tee "${REPORT_PATH%.json}.trtexec.log"
else
  echo "INFO: trtexec unavailable; Python TensorRT deserialization was used." >&2
fi

after_hash="$(sha256sum "${ENGINE_PATH}" | awk '{print $1}')"
after_size="$(stat -c '%s' "${ENGINE_PATH}")"
if [[ "${before_hash}" != "${after_hash}" || "${before_size}" != "${after_size}" ]]; then
  echo "BLOCKED: protected engine changed during validation" >&2
  exit 4
fi
echo "engine_read_only_validation=true"
echo "engine_sha256=${after_hash}"
echo "engine_size_bytes=${after_size}"
