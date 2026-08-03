#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALL_ROOT="${GAP_PLOT_AI_INSTALL_ROOT:-/home/dji/gap_plot_ai_dev}"
VENDOR_ROOT="${GAP_PLOT_AI_VENDOR_ROOT:-/home/dji/vendor}"
APP_COMMIT="@APP_COMMIT@"
PSDK_COMMIT="@PSDK_COMMIT@"
APP_RELEASE="${INSTALL_ROOT}/releases/${APP_COMMIT}"
APP_SOURCE="${APP_RELEASE}/source"
APP_SOURCE_ARCHIVE="${PACKAGE_ROOT}/sources/Drone-AI-source.tar"
APP_INFO_SOURCE="${PACKAGE_ROOT}/credentials/dji_sdk_app_info.local.h"
PSDK_RELEASE="${VENDOR_ROOT}/Payload-SDK-@PSDK_TAG@-${PSDK_COMMIT:0:12}"
ACTIVATE=0

if [[ "${1:-}" == "--activate" ]]; then
  ACTIVATE=1
elif [[ -n "${1:-}" ]]; then
  echo "Usage: $0 [--activate]" >&2
  exit 2
fi

"${SCRIPT_DIR}/verify_offline.sh"
mkdir -p "${INSTALL_ROOT}/releases" "${VENDOR_ROOT}"

if [[ -e "${APP_RELEASE}" ]]; then
  if [[ ! -d "${APP_SOURCE}" ]] || \
     [[ "$(cat "${APP_RELEASE}/source_commit.txt" 2>/dev/null || true)" != "${APP_COMMIT}" ]]; then
    echo "Existing application release does not match package; refusing overwrite." >&2
    exit 3
  fi
  echo "application_release_reused=${APP_RELEASE}"
else
  STAGING_RELEASE="$(mktemp -d "${INSTALL_ROOT}/releases/.staging-${APP_COMMIT:0:12}.XXXXXX")"
  cleanup_staging() {
    if [[ -n "${STAGING_RELEASE:-}" && -d "${STAGING_RELEASE}" ]]; then
      rm -rf -- "${STAGING_RELEASE}"
    fi
  }
  trap cleanup_staging EXIT INT TERM
  STAGING_SOURCE="${STAGING_RELEASE}/source"
  mkdir -p "${STAGING_SOURCE}/config"
  tar -xf "${APP_SOURCE_ARCHIVE}" -C "${STAGING_SOURCE}" --strip-components=1
  install -m 0600 "${APP_INFO_SOURCE}" \
    "${STAGING_SOURCE}/config/dji_sdk_app_info.local.h"
  printf '%s\n' "${APP_COMMIT}" > "${STAGING_RELEASE}/source_commit.txt"
  if [[ -d "${PACKAGE_ROOT}/offline_wheels" ]]; then
    cp -R "${PACKAGE_ROOT}/offline_wheels" "${STAGING_RELEASE}/offline_wheels"
  fi
  mv "${STAGING_RELEASE}" "${APP_RELEASE}"
  STAGING_RELEASE=""
  trap - EXIT INT TERM
fi

if [[ -e "${PSDK_RELEASE}" ]]; then
  if [[ ! -d "${PSDK_RELEASE}/.git" ]] || \
     [[ "$(git -C "${PSDK_RELEASE}" rev-parse HEAD 2>/dev/null || true)" != "${PSDK_COMMIT}" ]]; then
    echo "Existing PSDK release does not match package; refusing overwrite." >&2
    exit 4
  fi
  echo "psdk_release_reused=${PSDK_RELEASE}"
else
  git clone --no-checkout \
    "${PACKAGE_ROOT}/bundles/Payload-SDK-3.16.0.bundle" "${PSDK_RELEASE}"
  git -C "${PSDK_RELEASE}" remote set-url origin "@PSDK_ORIGIN@"
  git -C "${PSDK_RELEASE}" checkout --detach "${PSDK_COMMIT}"
fi

test "$(cat "${APP_RELEASE}/source_commit.txt")" = "${APP_COMMIT}"
test "$(git -C "${PSDK_RELEASE}" rev-parse HEAD)" = "${PSDK_COMMIT}"
python3 "${APP_SOURCE}/scripts/psdk_app_info.py" \
  "${APP_SOURCE}/config/dji_sdk_app_info.local.h"
python3 "${APP_SOURCE}/scripts/verify_psdk_316.py" "${PSDK_RELEASE}"

printf '%s\n' \
  "application_source=${APP_SOURCE}" \
  "application_commit=${APP_COMMIT}" \
  "psdk_root=${PSDK_RELEASE}" \
  "offline_wheels=${APP_RELEASE}/offline_wheels" \
  "psdk_commit=${PSDK_COMMIT}" > "${APP_RELEASE}/offline_release.env"

if [[ "${ACTIVATE}" -eq 1 ]]; then
  CURRENT_LINK="${INSTALL_ROOT}/current"
  if [[ -e "${CURRENT_LINK}" && ! -L "${CURRENT_LINK}" ]]; then
    echo "Refusing to replace non-symlink legacy path: ${CURRENT_LINK}" >&2
    exit 5
  fi
  if [[ -L "${CURRENT_LINK}" ]]; then
    readlink "${CURRENT_LINK}" > "${APP_RELEASE}/previous_current.txt"
  fi
  ln -sfn "${APP_RELEASE}" "${CURRENT_LINK}"
  echo "activated=${CURRENT_LINK}"
else
  echo "installed_not_activated=${APP_RELEASE}"
fi

echo "PSDK_ROOT=${PSDK_RELEASE}"
echo "No legacy source was deleted."
