# Readiness build, package, dan upload Manifold 3

Status 31 Juli 2026: **belum siap upload/install DPK**.

Dokumen ini membedakan development transfer, build target, packaging DPK, dan
operasi instalasi. Semua command perangkat di bawah adalah prosedur nanti;
belum dijalankan dalam audit.

## Yang sudah siap

- source native dikunci ke Linux aarch64, C++17, CMake minimum 3.16, dan PSDK
  resmi 3.16.0;
- target binary, template DPK, dan nama service konsisten sebagai
  `gap_plot_ai`;
- identitas aplikasi adalah `ggp-drone-ai`, App ID `189927`, versi aplikasi
  `00.01.00.00`;
- HAL USB bulk Manifold 3 dan `DJI_USE_ONLY_USB_BULK_DEVICE` dipertahankan;
- source Python target lulus kontrak Python 3.8;
- runtime membuat `data/logs` serta `runtime/ipc`, `runtime/reports`, dan
  session bounded;
- build/engine/runtime melakukan preflight kapasitas disk;
- report, credential, build output, dan DPK tetap ignored Git; model PT/ONNX
  wajib tersedia melalui Git LFS, sedangkan engine ditambahkan setelah build target.

## Blocker sebelum build AI

- ONNX, PyTorch, dan Ultralytics belum tersedia pada Manifold;
- kompatibilitas wheel target dengan Python 3.8/aarch64/CUDA 11.4 belum
  dibuktikan;
- ONNX kedua model sudah tersedia melalui Git LFS dan checksum-nya diverifikasi;
- engine FP16 belum dibangun dan belum diuji;
- credential lokal lengkap termasuk `DJI_DEVELOPER_ACCOUNT` belum boleh
  diasumsikan hanya dari report.

Setelah dependency target disiapkan secara terpisah dan disetujui operator,
jalankan preflight read-only pada Manifold:

```bash
cd /home/dji/gap_plot_ai_dev/source
.venv/bin/python scripts/check_manifold_ai_runtime.py --phase engine
.venv/bin/python scripts/check_manifold_ai_runtime.py --phase runtime
```

Keduanya harus berakhir dengan `ready=yes`.

## Build target nanti

Project mendukung build native langsung pada Manifold 3. Command ini belum
dijalankan:

```bash
export PSDK_ROOT="/path/on/manifold/Payload-SDK-3.16.0"
export GAP_PLOT_AI_APP_ROOT="/home/dji/gap_plot_ai_dev"

"$GAP_PLOT_AI_APP_ROOT/source/scripts/build_psdk_sample.sh"
"$GAP_PLOT_AI_APP_ROOT/source/scripts/build_engine.sh"
"$GAP_PLOT_AI_APP_ROOT/source/scripts/benchmark_engine.sh"
"$GAP_PLOT_AI_APP_ROOT/source/scripts/build_manifold.sh"
```

Hasil native yang diharapkan adalah
`$GAP_PLOT_AI_APP_ROOT/build/bin/gap_plot_ai`. Hasil engine harus tetap berada
di `runtime/models` dan memiliki build report/checksum.

Build dari laptop hanya sah bila laptop menggunakan Linux cross-toolchain
aarch64 yang kompatibel dengan GCC 9.4 dan sysroot target. macOS ARM64 bukan
Linux aarch64 dan tidak menghasilkan binary Manifold yang valid.

## Gate package DPK

`scripts/build_dpk.sh` masih sengaja gagal. Jangan membuka blokir sampai semua
syarat berikut terpenuhi:

- official sample, PSDK activation, M4E liveview, widget, telemetry, parity,
  dan ground test lulus;
- `ver_min` dan `ver_max` diambil dari versi firmware package Manifold yang
  benar, bukan kernel/L4T atau App ID;
- `app.json` final menunjuk binary dan config yang benar melalui path relatif;
- seluruh dependency runtime dapat dipaketkan sesuai aturan DJI: third-party
  library statis dan file runtime memakai path relatif;
- arsitektur Python/Ultralytics saat ini diganti atau dibuktikan melalui
  mekanisme packaging resmi DJI; menyalin venv/dynamic wheel ke DPK tidak
  dianggap valid;
- DPK kandidat serta satu DPK last-known-good memiliki checksum.

Setelah gate tersebut selesai dan staging package sudah diimplementasikan,
tool resmi yang digunakan adalah:

```bash
bash "$PSDK_ROOT/tools/build_dpk/build_dpk.sh" \
  -i "$GAP_PLOT_AI_APP_ROOT/dpk/staging/app.json" \
  -o "$GAP_PLOT_AI_APP_ROOT/dpk/build/ggp-drone-ai_v00.01.00.00.dpk"
```

Command di atas belum dapat dijalankan pada struktur saat ini karena staging
final belum dibuat.

## Upload dan install nanti

Source atau hasil build Linux dikirim dari laptop melalui SSH/SCP. Pilot 2
digunakan untuk validasi status, widget, dan liveview—bukan untuk upload source.
Aircraft bukan target penyalinan source melalui SD card.

Setelah DPK benar-benar lulus gate:

```bash
scp dpk/build/ggp-drone-ai_v00.01.00.00.dpk \
  dji@192.168.42.120:/home/dji/

ssh dji@192.168.42.120
dji_app_ctl install -i /home/dji/ggp-drone-ai_v00.01.00.00.dpk
dji_app_ctl status ggp-drone-ai
dji_app_ctl start ggp-drone-ai
dji_app_ctl status ggp-drone-ai
```

Instalasi hanya dilakukan saat Manifold terhubung benar ke aircraft dan
aircraft menyala, sesuai prosedur DJI. Command tersebut tidak dijalankan oleh
audit ini.

## Rollback aman

Sebelum update, simpan DPK last-known-good dan checksum-nya di laptop. Jika
custom app gagal start:

```bash
dji_app_ctl status ggp-drone-ai
dji_app_ctl stop ggp-drone-ai
dji_app_ctl install -i /home/dji/ggp-drone-ai_LAST_KNOWN_GOOD.dpk
dji_app_ctl status ggp-drone-ai
```

`install -i` juga digunakan DJI untuk update. Untuk instalasi pertama yang
gagal dan meninggalkan entry custom, uninstall hanya boleh menargetkan nama
yang sudah diverifikasi:

```bash
dji_app_ctl list ggp-drone-ai
dji_app_ctl uninstall ggp-drone-ai
```

Jangan pernah menjalankan stop, uninstall, atau update terhadap
`Smart3DExplore` maupun aplikasi DJI lainnya.
