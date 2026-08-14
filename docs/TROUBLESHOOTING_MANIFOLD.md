# Troubleshooting Manifold

Panduan ini disusun dari source canonical dan evidence snapshot. Panduan ini tidak menggantikan dokumentasi DJI atau prosedur safety operator.

## Prinsip Pertama

Selalu pastikan versi yang sedang dibahas:

```text
source development terbaru: v27
runtime terakhir terinstall: v26
```

Jangan menganggap perubahan source sudah ada pada Manifold sampai versi tersebut benar-benar dibuild, dipackage, diinstall, dan divalidasi.

## Aplikasi Tidak Start

Periksa:

- app terinstall sebagai `ggp-drone-ai`;
- runtime root pada Manifold berada di `/open_app/ggp-drone-ai`;
- native binary ada dan executable;
- credential DJI lokal sudah dikompilasi ke binary;
- `dji_app_ctl status ggp-drone-ai` memberi status yang masuk akal;
- blackbox log tersedia di `/blackbox/system/app_temp_files/ggp-drone-ai_*.log`.

Indikasi yang pernah relevan:

- `DjiCore_Init gagal`: kemungkinan credential/app binding;
- app langsung exit: kemungkinan env/path/worker startup;
- tidak ada blackbox log: app manager mungkin tidak menjalankan binary.

## PSDK Init Gagal

Source v27 memanggil:

```text
DjiCore_Init()
ValidateAircraft()
DjiCore_SetAlias()
DjiCore_SetSerialNumber()
DjiCore_SetFirmwareVersion()
DjiCore_ApplicationStart()
```

Periksa:

- aplikasi berjalan di Manifold 3, bukan host Mac;
- Matrice 4E tersambung pada Extension Port;
- credential DJI valid secara lokal;
- PSDK 3.16.0 digunakan;
- pengujian installed runtime dilakukan melalui DJI app manager.

Jangan print atau commit nilai credential.

## Liveview Tidak Masuk

v27 membutuhkan decoded RGB mode:

```text
GAP_PLOT_AI_LIVEVIEW_INPUT_MODE=decoded_rgb
DJI_LIVEVIEW_CAMERA_SOURCE_M4E_VIS
PIXFMT_RGB_PACKED
```

`StartLiveview()` tidak mengaktifkan fallback H.264. Jika decoded RGB stream gagal, cek return code dari `DjiLiveview_StartImageStream()`.

Evidence runtime v26 yang baik berisi:

```text
Decoded M4E RGB liveview subscription active.
```

## Frame RGB Tidak Valid

`ImageCallback()` menolak frame bila:

- buffer null;
- pixel format bukan `PIXFMT_RGB_PACKED`;
- width/height/stride tidak konsisten;
- data length tidak cocok dengan `row_stride * height`.

`worker.read_frame()` menolak spool bila:

- header terlalu pendek;
- magic/version/channels tidak cocok;
- row stride lebih kecil dari packed RGB;
- payload length tidak sesuai header.

Gejala:

- `worker_status.json` memiliki `frame_error`;
- AI FPS tetap nol;
- `latest_result.txt` tidak muncul.

## Python Worker Tidak Berjalan

Native v27 memiliki `SpawnPythonWorker()` dan mencoba:

```text
<app_root>/payload/python/bin/python3
```

Jika tidak ada, fallback ke:

```text
/usr/bin/python3.8
```

Periksa:

- `GAP_PLOT_AI_APP_ROOT`;
- `GAP_PLOT_AI_IPC_DIR`;
- `PYTHONPATH`;
- payload Python tersedia;
- package `gap_plot_ai.worker` ada di payload site-packages;
- `worker_status.json` heartbeat berjalan.

Known issue: launcher dan native spawn tidak boleh sama-sama menjalankan worker. Pilih satu path sebelum packaging v27.

## TensorRT Engine Gagal Load

Path engine pada Manifold:

```text
/home/dji/gap_plot_ai_assets/models/engine/plant_center_manual_v1_b0_best_fp16.engine
/home/dji/gap_plot_ai_assets/models/engine/plot_segmenter_b4_selected_best_fp16.engine
```

Periksa:

- file engine ada pada Manifold;
- hash sesuai jika memakai engine snapshot yang sama;
- `tensorrt` Python import berhasil;
- CUDA 11.4 tersedia;
- engine dibuat untuk TensorRT/CUDA/aarch64 yang kompatibel;
- `config/live.yaml` menunjuk path engine yang benar.

Jangan memakai engine yang dibuat pada macOS, Windows x86, GPU lain, CUDA lain, atau TensorRT lain.

## Bounding Box Tidak Tampil Di Pilot

Flow overlay:

```text
detector output
  -> latest_result.txt
  -> ParseResultFile()
  -> BuildPilotMetadata()
  -> DjiLiveview_SendAiMetaToPilot()
  -> DJI Pilot
```

Periksa:

- `latest_result.txt` berisi baris `BOX`;
- koordinat box valid dan normalized;
- stale timeout tidak membersihkan result terlalu cepat;
- return code `DjiLiveview_SendAiMetaToPilot()`;
- jumlah box dalam cap native;
- DJI Pilot menampilkan camera/source yang benar.

Source v27 memperlakukan kegagalan overlay sebagai degraded/non-fatal untuk AI pipeline.

## Session Tidak Finalize

Finalization berjalan di:

```text
src/gap_plot_ai/runtime.py::InferenceRuntime.stop()
```

Periksa:

- Stop AI benar-benar dipicu;
- session directory ada;
- `detections.jsonl` ada;
- worker tidak crash sebelum stop;
- `finalization_summary.json` dibuat;
- log/stderr memiliki `[V22] finalizer_started` dan `[V22] finalizer_complete`.

Known issue: `RegistrationFrameWriter` perlu review karena writer di-reset ke `None` pada source v27.

## Gap Coordinate Salah

Pertama tentukan versi:

- v26 runtime memiliki known Blocker-001;
- v27 source memperbaiki Blocker-001;
- v27 belum terbukti sebagai installed runtime.

Jika output berasal dari v26, posisi gap salah dapat sesuai known blocker. Jika output diklaim v27, verifikasi:

- deployed payload benar-benar berisi `src/gap_plot_ai/finalizer.py` v27;
- `_row_coords_to_local()` ada;
- evidence T01/T02/T03 sudah diterima atau direproduksi;
- session output bukan session lama dari v26.

## DPK / Install Issue

Periksa:

- version metadata sudah direkonsiliasi;
- `dpk/app.json.in` cocok dengan app identity/version yang dipilih;
- `scripts/build_dpk.sh` tidak membawa version stale;
- staging membawa native binary, Python payload, config, dan widget assets;
- staging tidak membawa `.engine`;
- builder resmi PSDK dipakai.

Jangan membuat DPK v27 sebelum version drift dan startup path selesai direview.

## DJI Developer / Binding

Credential lokal berada di:

```text
config/dji_sdk_app_info.local.h
```

File ini di-ignore dan tidak boleh dicommit. Jika binding gagal, investigasi hanya secara lokal pada target environment dan jangan menyalin nilai credential ke issue, dokumen, atau chat.
