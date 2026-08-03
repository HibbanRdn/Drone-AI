# Gap Plot AI DEV — live inference Manifold 3

## Status verifikasi

Host development sudah memverifikasi state machine, Start/Stop/restart,
single model lifecycle, latest-frame replacement, native-resolution tiling,
TorchVision global NMS, overlay mapping/limit, telemetry association, session
logging, busy shutdown, stream error, missing engine, offline CLI, dan replay
end-to-end dengan **fake backend**. Replay tidak membuktikan TensorRT, PSDK,
Pilot 2, ARM64, atau DPK pada perangkat.

Kontrak API diperiksa terhadap upstream resmi PSDK `3.16.0`, commit
`9af08536df3671ed77aee02f1f2e4173b0e0f558`. Header mengonfirmasi M4E_VIS,
decoded `PIXFMT_RGB_PACKED` khusus Manifold 3, image callback, metadata Pilot,
coordinate space 0–10.000, dan `boxCount` bertipe `uint8_t`. Karena itu
`overlay.max_objects: auto` menjadi batas ABI 255; batas operasional Pilot
tetap harus diuji pada hardware.

Checkout vendor diberikan melalui `PSDK_ROOT`; CMake juga membaca environment
tersebut secara langsung. Verifier menolak fork/mirror, versi/tag lain, vendor
diff, library aarch64 dengan checksum berbeda, dan sample/header Liveview yang
tidak lengkap.

Source Manifold tervalidasi tidak dapat dibaca karena SSH `192.168.42.120`
timeout. Benchmark TensorRT 30 frame dan keputusan
`enable_center_suppression: false` diperlakukan sebagai kontrak operator,
bukan sebagai pengujian ulang oleh host.

## Arsitektur aktual

```text
Pilot 2 widget
  -> PSDK C++ control/state
  -> M4E RGB callback (validate + replace latest only)
  -> /dev/shm/ggp-drone-ai/latest_frame.rgb (binary v2, atomic, capacity 1)
  -> persistent Python worker
       -> one detector load + one backend context + one warm-up
       -> native frame tiles 1024 / overlap 128
       -> TensorRT engine
       -> OpenCV native per-class global NMS (no Torch runtime)
       -> full detections + metrics + telemetry JSONL
       -> confidence-ranked Pilot subset
  -> PSDK AI metadata on normal Pilot liveview
```

Callback tidak menjalankan inference, NMS, drawing, JSON, session logging,
metadata transmission, atau H.264 encoding. IPC frame adalah packed binary
RGB pada Linux tmpfs, bukan JSON/base64. Pending frame lama diganti.

Metadata bounding box adalah jalur default. Full-frame rendered H.264 hanya
aktif bila `GAP_PLOT_AI_ENABLE_RENDERED_STREAM=1`; default `0` mempertahankan
normal liveview dan recording DJI. Plot segmenter dimatikan pada live config
karena plot contour bukan gap detection dan rendered-stream path belum lulus
hardware test.

Jika decoded RGB ditolak firmware/PSDK, app masuk error
`LIVEVIEW_STREAM_ERROR`, tetap hidup, dan mencoba stop/start subscription
dengan interval terkonfigurasi. Source tidak membuat decoder H.264 fiktif.
Sample resmi H.264 memakai `DJICameraStreamDecoder` dan FFmpeg 4.x; fallback
baru boleh diaktifkan setelah dependency itu dibangun dan diuji pada target.

## Config inference

`config/live.yaml` meng-extend `config/app.yaml`; tile, input, threshold, dan
NMS tetap satu sumber.

```yaml
live:
  input_mode: decoded_rgb
  decoded_pixel_format: RGB_PACKED
  h264_fallback:
    enabled: false
    frame_queue_size: 1
    drop_old_frames: true
  target_inference_fps: 1.5
  frame_queue_size: 1
  drop_old_frames: true
  batch_size: 1
  warmup_before_running: true
  stream_timeout_ms: 5000
  reconnect_interval_ms: 5000
models:
  detector:
    tile_size: 1024
    tile_overlap: 128
    global_nms_backend: opencv
    enable_center_suppression: false
  segmenter:
    enabled: false
overlay:
  max_objects: auto
  min_confidence: existing
  stale_result_timeout_ms: 1500
```

Validator menolak queue/batch selain 1, Python global NMS, dan pengaktifan
kembali center suppression. Grid berasal dari dimensi aktual; 1080p tidak
di-upscale ke 4K dan tidak ada asumsi frame selalu 3840×2160.

## Build dan test host

```bash
PYTHON_BIN=python3.10 ./scripts/bootstrap_dev.sh
.venv/bin/python -m pytest -q
./scripts/run_replay_test.sh
./scripts/run_replay_test.sh /path/to/replay.mp4

PSDK_ROOT=/path/to/official/Payload-SDK-3.16.0 \
  ./scripts/build_local.sh
```

Replay mencetak `backend: fake` dan `tensorrt_verified: false`. MP4 replay
tidak dipakai sebagai telemetry live. Offline CLI tetap tersedia:

```bash
./scripts/run_offline.sh /path/to/input.mp4 onnx 6
```

## Sinkronisasi dan run Manifold

Jangan memasang wheel host. Sebelum run, sinkronkan source/artefak yang benar
dipakai benchmark device:

1. engine lama tervalidasi read-only di
   `/home/dji/gap_plot_ai_assets/models/engine`;
2. wrapper TensorRT/device yang menghasilkan benchmark;
3. metadata binding engine dan decoder output harus cocok dengan kontrak ONNX;
4. checksum engine, input, threshold, tile, overlap, OpenCV NMS, dan
   `center_suppression=false`;
5. bukti `model_load_count=1`, `backend_initialization_count=1`,
   `warmup_count=1`; direct runtime membuat satu execution context serta buffer
   host-pinned/device persisten dan tidak memakai `model.names` Ultralytics.

Environment native untuk timeout, reconnect, heartbeat, dan stale overlay
dirender dari config tervalidasi oleh `scripts/live_config_env.py`; launcher
tidak menyimpan salinan angka yang berbeda.

```bash
export PSDK_ROOT=/path/to/official/Payload-SDK-3.16.0
export GAP_PLOT_AI_APP_ROOT=/home/dji/gap_plot_ai_dev/source

./scripts/build_psdk_sample.sh
./scripts/mark_psdk_verified.sh --official-sample-liveview-and-widget-passed
./scripts/check_manifold_ai_runtime.py --phase runtime
./scripts/build_manifold.sh
./scripts/run_static_overlay_test.sh
./scripts/run_live_dev.sh
```

Device launcher memakai backend `engine`, bukan `auto`; engine hilang menjadi
error dan tidak diam-diam fallback ke PT/ONNX. Known runtime errors menjaga
worker hidup agar Stop→Start dapat retry. Unexpected worker death dideteksi
heartbeat sehingga UI tidak tetap RUNNING.

## Pilot 2 dan keselamatan

Alias aplikasi `Gap Plot AI DEV`. Widget menyediakan Start Live AI, Stop Live
AI, plant switch, gap switch berlabel `N/A`, snapshot, dan IDLE/STARTING/
WARMING UP/RUNNING/STOPPING/ERROR. Floating status menampilkan source FPS,
AI FPS, latency, rolling p50, total plant detection, jumlah dikirim ke Pilot,
gap `N/A`, RTK/GPS, storage/session state, model/context/warm-up count, dropped
frames, serta pesan error.

Aplikasi tidak memanggil flight control, joystick, waypoint, takeoff/landing,
gimbal control, camera control, atau recording control. Rekaman 4K tetap
melalui tombol DJI normal.

## Session

Session ID memakai UTC, contoh `20260803T061827.661322Z`:

```text
runtime/sessions/<session_id>/
  session.json
  session_summary.json
  detections.jsonl
  telemetry.jsonl
  metrics.jsonl
  app.log
  errors.log
  frames/
  overlays/
  snapshots/
```

Semua detection disimpan; overlay Pilot hanya subset. Record memuat schema,
frame sequence, source frame ID, UTC dan monotonic timestamp, resolution,
pixel format, row stride, total/overlay count, timing, dan telemetry. Sampling
frame/overlay default off. Session/JSONL bounded dan runtime output ignored.

Telemetry live berasal dari PSDK: position, altitude, aircraft attitude,
gimbal attitude, velocity, GPS quality/satellites, dan RTK state bila topic
didukung. Zoom/focal tetap null sampai getter read-only resmi untuk firmware
target diverifikasi. Tidak ada SHP atau gap geolocation.

## DPK

```bash
./scripts/build_dpk.sh --check
./scripts/package_dpk.sh
```

DPK final hanya dibangun di Linux aarch64. Gate mewajibkan PSDK liveview,
engine parity, ground test, runtime bundle, firmware `ver_min/ver_max`, native
binary, Python target bundle, dan engine. Script membuat staging, menolak file
`.env`/credential tambahan, memvalidasi manifest, memanggil tool resmi PSDK,
dan menulis SHA-256. Identitas PSDK sudah terkompilasi ke binary native. Ini
gate nyata, bukan DPK mock.

Tool DJI hanya memasukkan executable `bin` dan entry `userconfig`. Karena itu
bundle lengkap ditempatkan di `payload/` dan manifest memasukkan direktori itu:

```text
bin/gap_plot_ai_launcher
payload/bin/gap_plot_ai_native
payload/python/
payload/models/engine/
payload/share/config/
data/logs/
data/runtime/sessions/
```

Launcher menetapkan `GAP_PLOT_AI_APP_ROOT=payload` untuk model/config dan
`GAP_PLOT_AI_RUNTIME_ROOT=data/runtime` untuk session writable. Build final
memvalidasi ulang bahwa engine yang direferensikan config memang ada di
staging sebelum memanggil tool resmi.

Install DPK melalui menu application management Pilot 2 sesuai firmware
Manifold yang terpasang. Nama menu/media source persisnya adalah hardware gate
dan belum diverifikasi pada host. CLI berikut hanya untuk diagnosis lokal di
Manifold setelah package tersedia:

```bash
dji_app_ctl install -i /path/to/ggp-drone-ai_v00.01.00.00.dpk
dji_app_ctl status ggp-drone-ai
dji_app_ctl start ggp-drone-ai
```

## Deployment offline source + PSDK

Generator tersedia di `scripts/create_offline_deployment.py`. Generator menolak
worktree kotor, sehingga paket final sengaja belum boleh dibuat sebelum diff
disetujui dan di-commit. Paket final berisi snapshot aplikasi tanpa history,
bundle exact tag PSDK, credential staging, wheel target, manifest/SHA-256,
verifier, installer release non-destructive, build script, transfer `scp`, dan
rollback symlink. Lihat `docs/OFFLINE_DEPLOY_WINDOWS.md`.
