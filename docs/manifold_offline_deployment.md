# Deployment offline Drone-AI + PSDK 3.16.0

## Scope

Paket ini memindahkan source yang dapat diaudit, bukan engine AI. TensorRT
8.5.2.2, CUDA 11.4, Python 3.8.10, environment Python target, dan engine yang
sudah tervalidasi tetap berada di Manifold. Identitas aplikasi PSDK ikut di
area credential paket mode `0600`, bukan Git/source snapshot, sehingga tidak
memerlukan provisioning `.env` terpisah.
Tidak ada command ke Manifold yang dijalankan saat paket dibuat di Mac.

## Prasyarat lokal

```bash
cd /path/to/Drone-AI
export PSDK_ROOT="/path/to/Payload-SDK-3.16.0"
python3 scripts/verify_psdk_316.py "$PSDK_ROOT"
python3 scripts/create_offline_deployment.py \
  --psdk-root "$PSDK_ROOT" \
  --credentials config/dji_sdk_app_info.local.h \
  --wheels ../offline_wheels_py38_aarch64 --check
```

`--check` dan pembuatan final menolak:

- branch selain `feature/pilot-liveview-inference`;
- origin aplikasi selain `HibbanRdn/Drone-AI`;
- diff atau untracked file;
- checkout PSDK selain origin resmi, exact tag/commit 3.16.0, dan clean;
- `.venv`, engine, dataset, media, runtime output, credential tracked, private
  config, atau cache build.

Snapshot aplikasi dibuat dengan `git archive` tanpa riwayat atau object LFS.
PT/ONNX tidak dibutuhkan runtime; engine device-side tetap di lokasi protected.
PSDK 3.16.0 tidak memakai submodule atau LFS.

## Membuat paket setelah diff disetujui dan di-commit

```bash
python3 scripts/create_offline_deployment.py \
  --psdk-root "$PSDK_ROOT" \
  --credentials config/dji_sdk_app_info.local.h \
  --wheels ../offline_wheels_py38_aarch64 \
  --output "../offline-packages"
```

Output:

```text
gap_plot_ai_offline_<app-sha>_<utc>.tar.gz
gap_plot_ai_offline_<app-sha>_<utc>.tar.gz.sha256
```

Isi arsip:

```text
gap_plot_ai_offline_<app-sha>_<utc>/
  sources/Drone-AI-source.tar
  bundles/Payload-SDK-3.16.0.bundle
  credentials/dji_sdk_app_info.local.h
  offline_wheels/PyYAML-6.0.2-...-aarch64.whl
  install/
    install_offline.sh
    verify_offline.sh
    verify_package.py
    build_manifold_offline.sh
    README_TRANSFER.md
  manifest.json
  SHA256SUMS
```

Manifest mencatat repository, branch/tag, exact commit, kebijakan LFS,
exclusion, ukuran, dan SHA-256 setiap artifact. Sidecar memverifikasi arsip
sebelum diekstrak; `SHA256SUMS` memverifikasi isi setelah ekstraksi.

## Transfer dan instalasi

Command lengkap tersedia di `install/README_TRANSFER.md` di dalam paket. Alur
aman:

1. verifikasi SHA-256 di Windows;
2. `scp` arsip + sidecar ke `/home/dji/gap_plot_ai_transfer/`;
3. verifikasi sidecar dan isi paket di Manifold;
4. install sebagai release baru di
   `/home/dji/gap_plot_ai_dev/releases/<app-commit>/source`;
5. install PSDK di `/home/dji/vendor/Payload-SDK-3.16.0-<psdk-sha>`;
6. build official sample dan aplikasi dengan satu `PSDK_ROOT` yang sama;
7. validasi engine lama secara read-only di
   `/home/dji/gap_plot_ai_assets/models/engine`, lalu jalankan static overlay
   dan ground test;
8. aktivasi symlink hanya dengan flag `--activate` setelah verifikasi.

Installer tidak menghapus atau menimpa source legacy. Jika path `current` bukan
symlink, aktivasi ditolak. Bila ada symlink lama, targetnya direkam di
`previous_current.txt`; rollback mengembalikan symlink tanpa menghapus release.

## Batas verifikasi

Generator, manifest, exclusion, template, wheel hash, dan checksum dapat diuji
di macOS.
Build Linux aarch64, aktivasi identitas PSDK, decoded/H.264 stream, overlay
Pilot, DPK, TensorRT, serta aircraft reconnect hanya dapat diverifikasi pada
Manifold 3 + Matrice 4E + Pilot 2.
