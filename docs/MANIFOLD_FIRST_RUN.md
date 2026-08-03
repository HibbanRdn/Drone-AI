# Manifold first run (offline, tanpa sudo)

Semua command dijalankan sebagai user `dji`. Jangan aktifkan motor atau terbang
pada tahap ini. Jangan menjalankan installer network, `sudo`, build engine,
refit, atau serialisasi engine.

```bash
set -euo pipefail
cd /home/dji/gap_plot_ai_transfer
PACKAGE="/home/dji/gap_plot_ai_transfer/PASTE_EXACT_PACKAGE_NAME.tar.gz"
test -f "${PACKAGE}"
test -f "${PACKAGE}.sha256"
sha256sum -c "${PACKAGE}.sha256"
tar -tzf "${PACKAGE}"
tar -xzf "${PACKAGE}"
PACKAGE_ROOT="${PACKAGE%.tar.gz}"
python3 "${PACKAGE_ROOT}/install/verify_offline_archive.py" \
  "${PACKAGE}" --sidecar "${PACKAGE}.sha256"
cd "${PACKAGE_ROOT}"
timeout 60s ./install/verify_offline.sh
timeout 120s ./install/install_offline.sh
```

Installer mencetak release source dan PSDK. Muat nilai yang dihasilkan tanpa
mengeksekusi file sebagai shell input:

```bash
APP_COMMIT="$(python3 -c 'import json; print(json.load(open("manifest.json"))["application"]["commit"])')"
RELEASE="/home/dji/gap_plot_ai_dev/releases/${APP_COMMIT}"
APP_SOURCE="${RELEASE}/source"
PSDK_ROOT="$(sed -n 's/^psdk_root=//p' "${RELEASE}/offline_release.env")"
export APP_SOURCE PSDK_ROOT
test -d "${APP_SOURCE}"
test -d "${PSDK_ROOT}"
```

## Inventory dan build

```bash
timeout 60s bash "${APP_SOURCE}/scripts/manifold_inventory_readonly.sh" \
  | tee "${APP_SOURCE}/runtime/reports/manifold_inventory.txt"
timeout 300s ./install/build_manifold_offline.sh
```

Build memverifikasi source PSDK exact, memasang PyYAML dari wheel offline ke
venv `--system-site-packages`, mengimpor source melalui `PYTHONPATH`, membangun
sample resmi dan native bridge, lalu memeriksa `ldd`. Ini belum menjalankan
aircraft Liveview atau TensorRT.

## Engine read-only

Tentukan nama engine yang benar-benar ada; jangan rename/copy/rebuild:

```bash
ENGINE_DIR=/home/dji/gap_plot_ai_assets/models/engine
find "${ENGINE_DIR}" -maxdepth 1 -type f -name '*.engine' -printf '%f\n'
export GAP_PLOT_AI_DETECTOR_ENGINE_PATH="${ENGINE_DIR}/PASTE_EXACT_ENGINE_FILENAME.engine"
timeout 90s bash "${APP_SOURCE}/scripts/validate_engine_readonly.sh"
timeout 120s bash "${APP_SOURCE}/scripts/manifold_diagnostics.sh"
```

Bandingkan `runtime/diagnostics/tensorrt_engine.json` dengan binding di
`docs/LIVEVIEW_PIPELINE.md`. Hash dan ukuran sebelum/sesudah harus identik.

## Ground gate berurutan

1. Aircraft di tanah, propeller aman, Pilot 2 aktif.
2. Jalankan sample resmi PSDK dan buktikan M4E_VIS decoded RGB + widget.
3. Baru buat marker dengan `mark_psdk_verified.sh` sesuai command help.
4. Build/run static five-box overlay dan periksa sudut/tengah.
5. Jalankan live app dengan engine override:

```bash
export GAP_PLOT_AI_APP_ROOT="${APP_SOURCE}"
export PYTHONPATH="${APP_SOURCE}/src"
export PSDK_ROOT
export GAP_PLOT_AI_DETECTOR_ENGINE_PATH
timeout 60s "${APP_SOURCE}/scripts/run_static_overlay_test.sh"
"${APP_SOURCE}/scripts/run_live_dev.sh"
```

Gunakan terminal lain untuk `status.sh`, `logs.sh`, `stop_dev.sh`. Jangan
menandai hardware checklist lulus tanpa bukti Pilot/aircraft. Aktivasi symlink
release dan DPK dilakukan terpisah setelah ground test; source legacy tidak
diubah.
