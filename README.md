# gap_plot_ai

Status 31 Juli 2026:

- `local_ready`: ya
- `model_export_ready`: ya
- `hardware_inventory_ready`: ya
- `psdk_blocked`: ya
- `ai_runtime_blocked`: ya
- `on_device_validated`: tidak
- `dpk_ready`: tidak

Identitas aplikasi:

| Peran | Nama/ID |
|---|---|
| DJI Developer App | `ggp-drone-ai` / App ID `189927` |
| Service/package internal | `gap_plot_ai` |
| DPK application identifier | `ggp-drone-ai` |
| Display alias Pilot 2 | `Gap Plot AI` |

Portal menunjukkan apply status `accepted` dan application status
`Not Verified`. Status ini tidak memblokir development/ground test pada satu
perangkat, tetapi distribusi aplikasi belum terverifikasi dibatasi maksimal 20
perangkat. Tidak ada perubahan dilakukan pada portal.

Ini adalah MVP onboard khusus DJI Matrice 4E + Manifold 3. Aplikasi tidak
memanggil flight control, joystick, waypoint, gimbal control, camera control,
training, cloud, S3, georeferencing, atau gap analysis final.

## Arsitektur

```text
M4E_VIS RGB frame (PSDK 3.16)
  -> C++ latest-frame spool (frame lama di-drop)
  -> Python worker
     -> detector: tile 1024, overlap 128, merge global
     -> segmenter: 1280, interval terpisah
  -> JSONL + telemetry + metrics + snapshot opsional
  -> C++ result reader
     -> bbox: PSDK AI recognition metadata (maks. 200 teratas ke Pilot 2)
     -> plot contour: digambar pada RGB AI-rendered stream
  -> PSDK H.264 encoder -> Pilot 2
  -> Custom Widget: Start/Stop, layer switches, Snapshot, status
```

Open AR tidak dipakai untuk contour piksel karena API itu menerima koordinat
geografis. Seluruh hasil detector tetap disimpan ke JSONL; batas 200 hanya
untuk metadata tampilan Pilot 2. Contour segmentasi disederhanakan dan
digambar pada frame AI sebelum dikirim melalui encoder resmi PSDK.

## Verifikasi lokal

```bash
cd "/Users/muhamadhibbanramadhan/Documents/Plot Gap"
export PSDK_ROOT="/path/to/official/Payload-SDK-3.16.0"
./manifold_app/scripts/bootstrap_dev.sh
./manifold_app/scripts/build_local.sh
./manifold_app/scripts/export_models.sh

VIDEO="/Users/muhamadhibbanramadhan/Movies/New Flight/recut_with_srt/pipeline_input_part1/DJI_20260729141517_0030_V_part1_000-037.MP4"
./manifold_app/scripts/parity.sh "$VIDEO" onnx 0 15 30
./manifold_app/scripts/run_offline.sh "$VIDEO" onnx 6
```

Artefak ONNX, report, session, snapshot, log, video preview, weight, dan
engine berada di path yang di-ignore Git.

## Baseline dan audit Manifold

Inventory read-only terbaru mengonfirmasi Ubuntu 20.04.6 aarch64 pada NVIDIA
Orin NX, Python 3.8.10, GCC 9.4, CMake 3.16.3, CUDA 11.4, cuDNN 8.6,
TensorRT 8.5.2, serta sekitar 8,7 GiB ruang kosong. `cv2` aktif adalah 4.5.4
dari `/usr/local`; ONNX, PyTorch, dan Ultralytics belum tersedia.
`dji_app_ctl` hanya menampilkan aplikasi resmi DJI `Smart3DExplore`;
`gap_plot_ai` belum terpasang.

Untuk mengulang inventory setelah laptop mempunyai interface debug
`192.168.42.x`:

```bash
export MANIFOLD_SSH_TARGET="dji@192.168.42.120"
mkdir -p runtime/reports
ssh -o ConnectTimeout=30 "$MANIFOLD_SSH_TARGET" \
  'bash -s' < scripts/manifold_inventory_readonly.sh \
  | tee runtime/reports/manifold_inventory_raw.txt
```

`bootstrap_dev.sh` adalah bootstrap host Python 3.10–3.12 dan sengaja
memblokir Linux aarch64. Source runtime mendukung Python 3.8, tetapi wheel
NumPy/OpenCV/PyTorch/Ultralytics target harus diaudit terhadap aarch64,
JetPack/L4T, CUDA 11.4, dan TensorRT 8.5.2 sebelum instalasi. Tidak ada
dependency atau aplikasi yang dipasang pada Manifold dalam audit ini.

Preflight target yang tidak menginstal apa pun:

```bash
.venv/bin/python scripts/check_manifold_ai_runtime.py --phase engine
.venv/bin/python scripts/check_manifold_ai_runtime.py --phase runtime
```

Credential portal tersimpan hanya di `config/secrets.env` yang ignored dan
berizin `600`. `scripts/psdk_credentials.py` memvalidasi ukuran buffer resmi
dan menghasilkan `runtime/generated/dji_sdk_app_info.generated.h` saat build.
Header generated juga ignored/berizin `600`, tidak mengubah header repo PSDK,
dan dilarang masuk DPK staging. Nilai sensitif tidak dicetak oleh validator.

PSDK juga mewajibkan `DJI_DEVELOPER_ACCOUNT` (email akun developer) pada
`T_DjiUserInfo.developerAccount[64]`. Field ini tidak ada pada screenshot dan
harus diisi lokal sebelum build perangkat; jangan kirim nilainya melalui chat.

## Gate sample resmi PSDK

Pada Manifold, gunakan checkout upstream resmi exact tag PSDK `3.16.0`
(commit `9af08536df3671ed77aee02f1f2e4173b0e0f558`). Script build memverifikasi
tag, commit, header versi, dan SHA-256 library aarch64 sebelum konfigurasi.
Target yang
dipakai adalah sample C++ `dji_sdk_demo_on_manifold3_cxx`; sample C tidak
diubah. Build menginjeksi header generated dengan compiler `-include`, sehingga
header resmi PSDK tetap tidak berisi credential plaintext.

```bash
export PSDK_ROOT="/path/on/manifold/Payload-SDK-3.16.0"
export GAP_PLOT_AI_APP_ROOT="/home/dji/gap_plot_ai_dev"
"$GAP_PLOT_AI_APP_ROOT/source/scripts/build_psdk_sample.sh"
```

Dengan aircraft tetap di tanah dan motor tidak dijalankan, sample harus
membuktikan product type M4E, telemetry, callback frame
`DJI_LIVEVIEW_CAMERA_SOURCE_M4E_VIS`, dan widget Pilot 2. Baru setelah bukti
tersebut:

```bash
"$GAP_PLOT_AI_APP_ROOT/source/scripts/mark_psdk_verified.sh" \
  --official-sample-liveview-and-widget-passed
```

## Build, engine, dan runtime Manifold

Command berikut adalah langkah nanti setelah dependency target dan gate sample
resmi lulus; belum dijalankan. TensorRT FP16 hanya dibangun pada Manifold atau
environment target identik:

```bash
export PSDK_ROOT="/path/on/manifold/Payload-SDK-3.16.0"
export GAP_PLOT_AI_APP_ROOT="/home/dji/gap_plot_ai_dev"

"$GAP_PLOT_AI_APP_ROOT/source/scripts/build_engine.sh"
"$GAP_PLOT_AI_APP_ROOT/source/scripts/benchmark_engine.sh"
"$GAP_PLOT_AI_APP_ROOT/source/scripts/parity.sh" \
  "/path/to/representative_test.mp4" engine 0 15 30
"$GAP_PLOT_AI_APP_ROOT/source/scripts/build_manifold.sh"
```

Setiap operasi melakukan preflight ruang kosong. Default minimum 1 GiB untuk
build/export/engine dan 1,5 GiB untuk runtime; dapat dinaikkan melalui variable
environment yang didokumentasikan script. Build engine memakai TensorRT
runtime dan tidak mewajibkan `nvcc`. ONNX adalah format pertukaran; engine dari
macOS, Windows x86, GPU lain, CUDA lain, atau TensorRT lain tidak digunakan.

`deploy_dev.sh` mengecualikan secret dari source archive, lalu mentransfer file
credential secara terpisah ke `${GAP_PLOT_AI_APP_ROOT}/config/secrets.env`
dengan permission `600`. Runtime native memakai identitas yang sudah
dikompilasi, tidak memasukkan `.env` ke DPK, serta membuat `data/logs` dan
direktori IPC sebelum `DjiCore_Init`. Runtime:

```bash
"$GAP_PLOT_AI_APP_ROOT/source/scripts/run_dev.sh"
"$GAP_PLOT_AI_APP_ROOT/source/scripts/status.sh"
"$GAP_PLOT_AI_APP_ROOT/source/scripts/logs.sh"
"$GAP_PLOT_AI_APP_ROOT/source/scripts/stop_dev.sh"
```

## DPK

`dpk/app.json.in` adalah template sumber, bukan package siap instal.
`scripts/build_dpk.sh` sengaja gagal sampai sample resmi, liveview, engine
FP16, parity, ground test, versi firmware untuk `ver_min/ver_max`, serta
strategi dependency statis DPK yang didukung DJI lulus. Worker
Python/Ultralytics saat ini belum dapat dianggap dependency DPK yang valid.
Tidak ada DPK yang dibuat atau diinstal.

Setelah gate DPK benar-benar lulus, command resmi pengelolaan aplikasi adalah
sebagai referensi operator dan belum dijalankan:

```bash
dji_app_ctl install -i /path/to/ggp-drone-ai_v00.01.00.00.dpk
dji_app_ctl status ggp-drone-ai
dji_app_ctl stop ggp-drone-ai
dji_app_ctl start ggp-drone-ai
dji_app_ctl list
```

Instalasi file versi baru digunakan untuk update. Jangan menjalankan
`uninstall` terhadap aplikasi lain dan jangan mengaktifkan auto-start sebelum
ground test stabil.

Lihat [audit lokal](docs/audit.md), [inventory
Manifold](docs/manifold_inventory.md), [readiness build/package/upload
Manifold](docs/manifold_readiness.md), [validasi
model](docs/model_validation.md), [validasi PSDK](docs/psdk_validation.md), dan
[ground test](docs/ground_test.md).

Referensi resmi: [Manifold quick
demo](https://developer.dji.com/doc/payload-sdk-tutorial/en/manifold-quick-start/quick-demo.html),
[Liveview](https://developer.dji.com/doc/payload-sdk-tutorial/en/advanced-function/liveview.html),
[Custom Widget](https://developer.dji.com/doc/payload-sdk-tutorial/en/basic-function/custom-widget.html),
[build DPK](https://developer.dji.com/doc/payload-sdk-tutorial/en/manifold-quick-start/build-dpk.html),
dan [application
management](https://developer.dji.com/doc/payload-sdk-tutorial/en/manifold-quick-start/manifold-platform-capabilities/system-tools.html).
