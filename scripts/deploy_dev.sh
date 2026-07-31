#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET="${MANIFOLD_SSH_TARGET:?Set MANIFOLD_SSH_TARGET, mis. alias SSH yang sudah dikonfigurasi}"
REMOTE_ROOT="${MANIFOLD_DEV_ROOT:-/home/dji/gap_plot_ai_dev}"
SECRETS="${GAP_PLOT_AI_SECRETS_FILE:-${SOURCE_ROOT}/config/secrets.env}"
ARCHIVE="$(mktemp -t gap_plot_ai_source.XXXXXX.tar.gz)"
trap 'rm -f "${ARCHIVE}"' EXIT

tar -C "${SOURCE_ROOT}" -czf "${ARCHIVE}" \
  --exclude=.venv --exclude=build --exclude=runtime --exclude='*.dpk' \
  --exclude=config/secrets.env --exclude='config/*.secret.*' .

ssh -o BatchMode=yes -o ConnectTimeout=5 "${TARGET}" \
  "mkdir -p '${REMOTE_ROOT}/source' '${REMOTE_ROOT}/runtime/models' '${REMOTE_ROOT}/config' && chmod 700 '${REMOTE_ROOT}/config'"
scp -q "${ARCHIVE}" "${TARGET}:${REMOTE_ROOT}/source/gap_plot_ai.tar.gz"
ssh -o BatchMode=yes "${TARGET}" \
  "tar -xzf '${REMOTE_ROOT}/source/gap_plot_ai.tar.gz' -C '${REMOTE_ROOT}/source'"

if [[ -f "${SECRETS}" ]]; then
  "${SOURCE_ROOT}/scripts/psdk_credentials.py" validate --secrets "${SECRETS}"
  scp -q "${SECRETS}" "${TARGET}:${REMOTE_ROOT}/config/secrets.env.tmp"
  ssh -o BatchMode=yes "${TARGET}" \
    "chmod 600 '${REMOTE_ROOT}/config/secrets.env.tmp' && mv '${REMOTE_ROOT}/config/secrets.env.tmp' '${REMOTE_ROOT}/config/secrets.env'"
fi

for model in plant_detector_b0_manual_v1_best plot_segmenter_b4_selected_best; do
  local_model="${SOURCE_ROOT}/runtime/models/${model}.onnx"
  if [[ -f "${local_model}" ]]; then
    scp -q "${local_model}" "${TARGET}:${REMOTE_ROOT}/runtime/models/"
  fi
done
echo "Development source transferred to ${TARGET}:${REMOTE_ROOT}"
