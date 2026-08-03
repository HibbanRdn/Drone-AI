#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_ROOT="${GAP_PLOT_AI_APP_ROOT:-${SOURCE_ROOT}}"
PSDK_ROOT="${PSDK_ROOT:-}"
STAGING="${APP_ROOT}/dpk/staging"
OUTPUT_DIR="${APP_ROOT}/dpk/build"
VERSION="00.01.00.00"
OUTPUT="${OUTPUT_DIR}/ggp-drone-ai_v${VERSION}.dpk"

if [[ "${1:-}" == "--check" ]]; then
  printf '%s\n' \
    "dpk_template=${SOURCE_ROOT}/dpk/app.json.in" \
    "native_binary=${APP_ROOT}/build/bin/gap_plot_ai" \
    "runtime_bundle=${APP_ROOT}/runtime/dpk_bundle" \
    "gate_psdk=${APP_ROOT}/runtime/gates/psdk_liveview_verified" \
    "gate_engine=${APP_ROOT}/runtime/gates/engine_parity_verified" \
    "gate_ground=${APP_ROOT}/runtime/gates/ground_test_verified" \
    "gate_bundle=${APP_ROOT}/runtime/gates/dpk_runtime_bundle_verified"
  exit 0
fi

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "aarch64" ]]; then
  echo "DPK final hanya boleh dibangun di Linux aarch64 Manifold 3." >&2
  exit 2
fi
if [[ -z "${PSDK_ROOT}" || ! -x "${PSDK_ROOT}/tools/build_dpk/build_dpk.sh" ]]; then
  echo "PSDK_ROOT harus menunjuk upstream resmi 3.16.0 dengan tools/build_dpk." >&2
  exit 3
fi
if [[ -z "${MANIFOLD_VER_MIN:-}" || -z "${MANIFOLD_VER_MAX:-}" ]]; then
  echo "Set MANIFOLD_VER_MIN dan MANIFOLD_VER_MAX dari firmware package Manifold." >&2
  exit 4
fi

required_paths=(
  "${APP_ROOT}/build/bin/gap_plot_ai"
  "${APP_ROOT}/runtime/dpk_bundle/python/bin/python3"
  "${APP_ROOT}/runtime/dpk_bundle/models/plant_detector.engine"
  "${APP_ROOT}/runtime/gates/psdk_liveview_verified"
  "${APP_ROOT}/runtime/gates/engine_parity_verified"
  "${APP_ROOT}/runtime/gates/ground_test_verified"
  "${APP_ROOT}/runtime/gates/dpk_runtime_bundle_verified"
)
for required in "${required_paths[@]}"; do
  if [[ ! -e "${required}" ]]; then
    echo "DPK gate/dependency belum tersedia: ${required}" >&2
    exit 5
  fi
done

"${SOURCE_ROOT}/scripts/check_disk_space.sh" \
  --path "${APP_ROOT}" --min-mib "${GAP_PLOT_AI_BUILD_MIN_FREE_MIB:-1536}" \
  --operation "DPK staging"

rm -rf "${STAGING}"
mkdir -p "${STAGING}/bin" "${STAGING}/payload/bin" \
  "${STAGING}/payload/share/config" "${OUTPUT_DIR}"
cp -R "${APP_ROOT}/runtime/dpk_bundle/." "${STAGING}/payload/"
install -m 0755 "${APP_ROOT}/build/bin/gap_plot_ai" \
  "${STAGING}/payload/bin/gap_plot_ai_native"
install -m 0755 "${SOURCE_ROOT}/scripts/dpk_launcher.sh" \
  "${STAGING}/bin/gap_plot_ai_launcher"
install -m 0755 "${SOURCE_ROOT}/scripts/live_config_env.py" \
  "${STAGING}/payload/bin/live_config_env.py"
mkdir -p "${STAGING}/payload/models/engine"
mv "${STAGING}/payload/models/plant_detector.engine" \
  "${STAGING}/payload/models/engine/plant_center_manual_v1_b0_tensorrt-8.5.2_cuda-11.4_aarch64_fp16.engine"
cp "${SOURCE_ROOT}/config/live.yaml" "${STAGING}/payload/share/config/live.yaml"
cp "${SOURCE_ROOT}/config/app.yaml" "${STAGING}/payload/share/config/app.yaml"
cp -R "${SOURCE_ROOT}/config/widget" "${STAGING}/payload/share/config/widget"

sed \
  -e "s/@MANIFOLD_VER_MIN@/${MANIFOLD_VER_MIN}/g" \
  -e "s/@MANIFOLD_VER_MAX@/${MANIFOLD_VER_MAX}/g" \
  "${SOURCE_ROOT}/dpk/app.json.in" > "${STAGING}/app.json"

python3 "${SOURCE_ROOT}/scripts/validate_dpk.py" \
  "${STAGING}/app.json" --staging-root "${STAGING}"

GAP_PLOT_AI_APP_ROOT="${STAGING}/payload" \
  GAP_PLOT_AI_RUNTIME_ROOT="${STAGING}/data/runtime" \
  "${STAGING}/payload/python/bin/python3" -c \
  'from gap_plot_ai.config import load_config; from gap_plot_ai.models import resolve_backend_path; c=load_config("'"${STAGING}"'/payload/share/config/live.yaml"); p=resolve_backend_path(c["models"]["detector"], "engine"); assert p.is_file(), p; print(f"dpk_engine_path={p}")'

bash "${PSDK_ROOT}/tools/build_dpk/build_dpk.sh" \
  -i "${STAGING}/app.json" -o "${OUTPUT_DIR}"
if [[ ! -f "${OUTPUT}" ]]; then
  echo "Tool resmi selesai tetapi DPK tidak ditemukan: ${OUTPUT}" >&2
  exit 6
fi
sha256sum "${OUTPUT}" > "${OUTPUT}.sha256"
printf '%s\n' "${OUTPUT}"
