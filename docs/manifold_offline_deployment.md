# Deployment offline Drone-AI + PSDK 3.16.0

## Scope

Paket ini memindahkan source yang dapat diaudit, bukan runtime AI. TensorRT
8.5.2.2, CUDA 11.4, Python 3.8.10, environment Python target, credential lokal,
dan engine yang sudah tervalidasi tetap berada di Manifold dan tidak dibundle.
Tidak ada command ke Manifold yang dijalankan saat paket dibuat di Mac.

## Prasyarat lokal

```bash
cd /path/to/Drone-AI
export PSDK_ROOT="/path/to/Payload-SDK-3.16.0"
python3 scripts/verify_psdk_316.py "$PSDK_ROOT"
python3 scripts/create_offline_deployment.py \
  --psdk-root "$PSDK_ROOT" --check
```

`--check` dan pembuatan final menolak:

- branch selain `feature/pilot-liveview-inference`;
- origin aplikasi selain `HibbanRdn/Drone-AI`;
- diff atau untracked file;
- checkout PSDK selain origin resmi, exact tag/commit 3.16.0, dan clean;
- `.venv`, engine, dataset, media, runtime output, credential, private config,
  atau cache build yang tracked.

Karena generator memakai Git bundle, PT/ONNX di branch hanya menjadi pointer
LFS. Object LFS tidak dimasukkan: source build/runtime Manifold memakai engine
device-side yang sudah tersedia. PSDK 3.16.0 tidak memakai submodule atau LFS.

## Membuat paket setelah diff disetujui dan di-commit

```bash
python3 scripts/create_offline_deployment.py \
  --psdk-root "$PSDK_ROOT" \
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
  bundles/
    Drone-AI.bundle
    Payload-SDK-3.16.0.bundle
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

1. verifikasi SHA-256 di Mac;
2. `scp` arsip + sidecar ke `/home/dji/offline-incoming/`;
3. verifikasi sidecar dan isi paket di Manifold;
4. install sebagai release baru di
   `/home/dji/gap_plot_ai_dev/releases/<app-commit>/source`;
5. install PSDK di `/home/dji/vendor/Payload-SDK-3.16.0-<psdk-sha>`;
6. build official sample dan aplikasi dengan satu `PSDK_ROOT` yang sama;
7. sinkronkan engine/runtime existing secara lokal, lalu jalankan static overlay
   dan ground test;
8. aktivasi symlink hanya dengan flag `--activate` setelah verifikasi.

Installer tidak menghapus atau menimpa source legacy. Jika path `current` bukan
symlink, aktivasi ditolak. Bila ada symlink lama, targetnya direkam di
`previous_current.txt`; rollback mengembalikan symlink tanpa menghapus release.

## Batas verifikasi

Generator, manifest, exclusion, template, dan checksum dapat diuji di macOS.
Build Linux aarch64, credential/activation PSDK, decoded/H.264 stream, overlay
Pilot, DPK, TensorRT, serta aircraft reconnect hanya dapat diverifikasi pada
Manifold 3 + Matrice 4E + Pilot 2.
