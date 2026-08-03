#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET="${MANIFOLD_SSH_TARGET:?Set MANIFOLD_SSH_TARGET, mis. alias SSH yang sudah dikonfigurasi}"
REMOTE_ROOT="${MANIFOLD_DEV_ROOT:-/home/dji/gap_plot_ai_dev}"
ARCHIVE="$(mktemp -t gap_plot_ai_source.XXXXXX.tar.gz)"
trap 'rm -f "${ARCHIVE}"' EXIT

tar -C "${SOURCE_ROOT}" -czf "${ARCHIVE}" \
  --exclude=.venv --exclude=build --exclude=runtime --exclude='*.dpk' \
  --exclude=config/secrets.env --exclude='config/*.secret.*' .

ssh -o BatchMode=yes -o ConnectTimeout=5 "${TARGET}" \
  "mkdir -p '${REMOTE_ROOT}/source' '${REMOTE_ROOT}/config' && chmod 700 '${REMOTE_ROOT}/config'"
scp -q "${ARCHIVE}" "${TARGET}:${REMOTE_ROOT}/source/gap_plot_ai.tar.gz"
ssh -o BatchMode=yes "${TARGET}" \
  "tar -xzf '${REMOTE_ROOT}/source/gap_plot_ai.tar.gz' -C '${REMOTE_ROOT}/source'"

printf '%s\n' \
  "Development source transferred to ${TARGET}:${REMOTE_ROOT}/source" \
  "Set GAP_PLOT_AI_APP_ROOT=${REMOTE_ROOT}/source" \
  "PSDK application identity is included in the private application source"
