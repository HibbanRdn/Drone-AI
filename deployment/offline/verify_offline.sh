#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

python3 "${SCRIPT_DIR}/verify_package.py" "${PACKAGE_ROOT}"
VERIFY_REPOSITORY="$(mktemp -d /tmp/gap_plot_ai_bundle_verify.XXXXXX)"
cleanup_verify_repository() {
  rm -rf -- "${VERIFY_REPOSITORY}"
}
trap cleanup_verify_repository EXIT INT TERM
git -C "${VERIFY_REPOSITORY}" init --quiet
git -C "${VERIFY_REPOSITORY}" bundle verify \
  "${PACKAGE_ROOT}/bundles/Drone-AI.bundle"
git -C "${VERIFY_REPOSITORY}" bundle verify \
  "${PACKAGE_ROOT}/bundles/Payload-SDK-3.16.0.bundle"

printf '%s\n' \
  "application_commit=@APP_COMMIT@" \
  "application_branch=@APP_BRANCH@" \
  "psdk_commit=@PSDK_COMMIT@" \
  "psdk_tag=@PSDK_TAG@" \
  "network_required=false"
