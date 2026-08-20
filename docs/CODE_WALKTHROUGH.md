# Source Code dan PSDK Walkthrough

Dokumen ini menelusuri source canonical v27 dari native entry point sampai output session. Nama file, class, dan function telah dicocokkan dengan source repository pada 2026-08-20. Perilaku yang belum tersambung atau belum diuji runtime diberi label eksplisit.

## 1. Code Map

```text
src/
|-- psdk/
|   `-- main.cpp                 native PSDK entry point
`-- gap_plot_ai/
    |-- worker.py                persistent IPC worker
    |-- runtime.py               model/session lifecycle
    |-- tensorrt_backend.py      direct CUDA/TensorRT execution
    |-- tiling.py                detector tile + global merge
    |-- overlay.py               bbox selection/coordinate mapping
    |-- storage.py               session, JSONL, logs, snapshots
    |-- finalizer.py             registration, fusion, gap analysis
    |-- spatial.py               dependency-free KD-tree
    |-- geospatial_v2.py         RTK/UTM/similarity utilities
    |-- registration_writer.py   asynchronous keyframe writer
    |-- shp_writer.py            SHP/SHX/DBF writer
    |-- schema.py                telemetry/model/frame dataclasses
    |-- state.py                 application state machine
    |-- config.py                YAML merge + validation
    |-- export_models.py         PT -> ONNX
    `-- build_engine.py          ONNX -> TensorRT FP16
```

Source development berikutnya dimulai dari tree ini. Folder `tools/` dan dokumen historis bukan runtime canonical.

## 2. End-to-End Ownership

```text
Matrice 4E camera
  -> src/psdk/main.cpp
     producer: decoded RGB + telemetry + controls
  -> /dev/shm/ggp-drone-ai
     latest-frame IPC
  -> worker.py
     consumer + scheduler + result producer
  -> runtime.py
     detector + segmenter + session
  -> tensorrt_backend.py
     GPU execution and postprocess
  -> latest_result.txt
  -> main.cpp
     Pilot metadata sender

Stop AI
  -> runtime.stop()
  -> finalizer.finalize_session()
  -> local GeoJSON/SHP/summary/preview
```

## 3. Native Includes dan Module PSDK

`src/psdk/main.cpp` menggunakan header:

| Header | API project | Peran resmi DJI / peran project |
|---|---|---|
| `dji_platform.h` | `DjiPlatform_RegOsalHandler`, `RegHalUsbBulkHandler`, `RegSocketHandler`, `RegFileSystemHandler` | mendaftarkan adapter OS/HAL sebelum core init |
| `dji_logger.h` | `DjiLogger_AddConsole` | mendaftarkan console logger PSDK |
| `dji_core.h` | `DjiCore_Init`, `SetAlias`, `SetSerialNumber`, `SetFirmwareVersion`, `ApplicationStart`, `DeInit` | lifecycle dan identity aplikasi PSDK |
| `dji_aircraft_info.h` | `DjiAircraftInfo_GetBaseInfo` | membaca aircraft/mount base info |
| `dji_widget.h` | `DjiWidget_Init`, register UI/handler, floating message | widget dan status pada Pilot |
| `dji_fc_subscription.h` | init/subscribe/get latest/deinit | telemetry aircraft |
| `dji_high_speed_data_channel.h` | `SetBandwidthProportion` | alokasi data/video/download bandwidth |
| `dji_liveview.h` | init/start decoded image/send AI metadata/encoder | frame acquisition dan Pilot overlay |
| `dji_payload_camera.h` | `SendVideoStream` | optional rendered H.264 return path |
| `dji_gimbal_manager.h` | init/set mode/reset | gimbal forward/nadir widget action |

Compile-time guards memastikan PSDK `3.16.0` dan `DJI_USE_ONLY_USB_BULK_DEVICE`.

## 4. PSDK API Yang Dipakai

### 4.1 Platform dan core

#### `DjiPlatform_Reg*Handler`

- **Asal**: `dji_platform.h`.
- **Fungsi resmi**: memberi PSDK implementasi OSAL, USB bulk, socket, dan filesystem platform.
- **Project**: `SetupPlatform()` mengisi handler dari official sample Manifold 3 (`osal/*`, `hal_usb_bulk.c`) lalu mendaftarkannya.
- **Urutan**: sebelum `DjiCore_Init()`.
- **Failure**: exception startup; app berhenti.

#### `DjiCore_Init`

- **Asal**: `dji_core.h`.
- **Fungsi resmi**: blocking core initialization dengan `T_DjiUserInfo`; dipanggil setelah console/OSAL/HAL dan sebelum module API.
- **Project**: mendapat identity dari `dji_sdk_app_info.local.h`, lalu menginisialisasi USB-bulk PSDK.
- **Failure**: cek credential lokal, aircraft power/link, firmware, PSDK, dan USB-bulk topology.

#### `DjiCore_SetAlias/SetSerialNumber/SetFirmwareVersion`

- alias `drone-ai` dan serial development di-set untuk identity Pilot;
- firmware struct source masih `0.1.0.25` (`debugVersion=25`), sedangkan DPK template `00.01.00.00` dan deployed v26 `00.01.00.26`;
- drift ini harus direkonsiliasi sebelum release v27/v28.

#### `DjiCore_ApplicationStart`

DJI menyatakan API ini dipanggil setelah semua module initialization/registration. Source saat ini memanggilnya setelah widget, tetapi sebelum `InitTelemetry()` dan `StartLiveview()`. Ini adalah **ORDERING REVIEW REQUIRED** untuk build hardware berikutnya; dokumentasi tidak mengubah behavior.

### 4.2 Aircraft validation

`ValidateAircraft()` memanggil `DjiAircraftInfo_GetBaseInfo()` dan menolak runtime jika:

- aircraft bukan `DJI_AIRCRAFT_TYPE_M4E`; atau
- mount bukan `DJI_MOUNT_POSITION_TYPE_MANIFOLD3_ONBOARD`.

Ini mencegah package dianggap generic untuk aircraft lain.

### 4.3 Widget

`InitWidget()`:

1. `DjiWidget_Init()`;
2. register UI directory Inggris sebagai default;
3. register Inggris dan Mandarin big-screen config;
4. register tiga handler.

Widget index:

| Index | Type | Callback effect |
|---:|---|---|
| 0 | switch | queue gimbal down/forward command |
| 1 | button | `running=true`, status STARTING |
| 2 | button | `running=false`, status STOPPING, clear overlay |

`SetWidgetValue()` menulis `control.txt` secara atomic. `GetWidgetValue()` dipanggil PSDK untuk sinkronisasi UI. `DjiWidgetFloatingWindow_ShowMessage()` menampilkan state, FPS, latency, plant/gap, RTK/GPS, model lifecycle, source resolution, drop count, dan session.

### 4.4 Telemetry subscription

`InitTelemetry()` memanggil `DjiFcSubscription_Init()` lalu polling-mode subscribe (callback `nullptr`):

| Topic | Frequency | Dipakai untuk |
|---|---:|---|
| `POSITION_FUSED` | 5 Hz | latitude, longitude, absolute altitude, satellite count |
| `HEIGHT_FUSION` | 50 Hz | relative altitude |
| `GIMBAL_ANGLES` | 50 Hz | roll/pitch/yaw gimbal |
| `QUATERNION` | 5 Hz | roll/pitch/yaw aircraft |
| `VELOCITY` | 5 Hz | xyz velocity dan speed |
| `GPS_SIGNAL_LEVEL` | 5 Hz | GPS quality |
| `RTK_POSITION_INFO` | 1 Hz | raw RTK status |

`BuildFrameHeader()` mengambil latest value dan mengisi valid bit mask. Topic yang unsupported tidak mematikan AI; nilainya menjadi null pada worker.

Source subscribe `GIMBAL_ANGLES` dua kali. Duplicate code ditoleransi oleh helper, tetapi tetap perlu dibersihkan/review saat runtime stabilization.

### 4.5 Liveview

`StartLiveview()`:

1. memastikan input mode `decoded_rgb`;
2. mengatur high-speed bandwidth `{data=10, video=60, download=30}`;
3. `DjiLiveview_Init()`;
4. register label `plant` dan `gap`;
5. optional register hardware encoder callback;
6. start decoded RGB subscription.

`DjiLiveview_StartImageStream()` dipanggil dengan:

```text
position = CAMERA_POSITION_NO_1
source   = CAMERA_SOURCE_M4E_VIS
format   = PIXFMT_RGB_PACKED
callback = ImageCallback
```

Decoded RGB API hanya didukung pada Manifold 3 menurut DJI. H.264 fallback disebut di config tetapi tidak dikompilasi dalam binary ini.

### 4.6 Pilot AI metadata

Flow:

```text
normalized bbox 0..1
  -> BuildPilotMetadata()
  -> center/width/height 0..10000
  -> top 62 by confidence
  -> T_DjiLiveViewStandardMetaData
  -> DjiLiveview_SendAiMetaToPilot()
```

Metadata send failure diperlakukan degraded/non-fatal: AI/session dapat tetap berjalan, tetapi overlay dikosongkan dan error code ditampilkan.

Optional rendered-stream path memakai `DjiLiveview_EncodeAFrameToH264()` dan `DjiPayloadCamera_SendVideoStream()`. Baseline `GAP_PLOT_AI_ENABLE_RENDERED_STREAM=0`; Pilot overlay utama adalah metadata box, bukan rendered contour video.

### 4.7 Gimbal manager

`InitGimbal()` menginisialisasi manager dan mode FREE pada payload port 1. Widget memanggil reset mode downward+yaw untuk nadir atau pitch+yaw untuk forward. DJI mendefinisikan reset sebagai command gimbal pada mount position tertentu. Hardware behavior harus divalidasi sebelum field use.

## 5. `main.cpp` Logical Flow

Urutan aktual `main()`:

1. `InitializeDerivedPaths()` menurunkan app root/payload/data/IPC/widget/PYTHONPATH.
2. `SpawnPythonWorker()` mencoba menjalankan worker dari bundled Python.
3. register SIGINT/SIGTERM.
4. validasi `GAP_PLOT_AI_IPC_DIR` dan read environment liveview/timeout.
5. `EnsureRuntimeDirectories()`.
6. `SetupPlatform()`.
7. `LoadUserInfo()`.
8. `DjiCore_Init()`.
9. `ValidateAircraft()`.
10. set alias, serial, firmware version.
11. `InitWidget()`.
12. `DjiCore_ApplicationStart()`.
13. `InitTelemetry()`.
14. `WriteControls()`.
15. `StartLiveview()`.
16. start `FrameSpoolThread`.
17. loop: gimbal command, parse result, send metadata, update status, retry stalled stream.
18. shutdown: stop worker, join spool, stop liveview, deinit telemetry/core.

### Startup duplication risk

`scripts/dpk_launcher.sh` juga menjalankan Python worker sebelum native binary. Native `main()` kemudian memanggil `SpawnPythonWorker()`. Dengan `GAP_PLOT_AI_APP_ROOT` terisi, dua worker dapat mencoba memakai IPC yang sama.

Status: **OPEN - launcher/native-worker startup reconciliation**. Pilih satu owner sebelum packaging v27; jangan menyatakan startup production siap sebelum diuji.

## 6. Frame Producer dan Latest-Only Backpressure

### `ImageCallback()`

- memvalidasi buffer, `PIXFMT_RGB_PACKED`, width/height, row stride, dan length;
- hanya menyimpan frame jika AI running atau rendered stream enabled;
- copy frame ke `FramePacket`;
- bila frame sebelumnya belum diproses, replace dan increment `g_droppedFrames`;
- signal spool thread.

### `FrameSpoolThread()`

- menunggu condition variable;
- mengambil latest packet;
- `BuildFrameHeader()` menambahkan telemetry;
- menulis header 196 byte + RGB rows ke temporary file;
- atomic rename menjadi `latest_frame.rgb`.

Queue tidak tumbuh. Latency dijaga dengan membuang intermediate frame, bukan memproses backlog.

## 7. IPC Contract C++ <-> Python

Default directory:

```text
/dev/shm/ggp-drone-ai
```

| File | Producer | Consumer | Contract |
|---|---|---|---|
| `control.txt` | C++ widget | worker | `running`, `snapshot_seq` key=value |
| `latest_frame.rgb` | C++ spool | worker | `GPAIFRM2` packed header + RGB payload |
| `latest_result.txt` | worker | C++ | RESULT/BOX/CONTOUR/END text protocol |
| `worker_status.json` | worker | C++/operator | state, heartbeat, session, metrics, warnings |

Stale protection:

- atomic temporary write + rename;
- source frame sequence deduplication;
- session start monotonic timestamp filter;
- target FPS rate limiting;
- result generated timestamp timeout;
- worker heartbeat timeout;
- stream reconnect interval.

## 8. `worker.py`

### `read_frame()`

1. membaca entire spool;
2. unpack `FRAME_HEADER` yang harus sama dengan C++ `FrameHeader`;
3. memvalidasi magic/version/channel/stride/length;
4. membentuk RGB array tanpa padding;
5. mengonversi RGB -> BGR dengan OpenCV;
6. membentuk `Telemetry`, `FrameMetadata`, dan `FrameEnvelope`.

### `run()`

1. load merged config;
2. resolve IPC paths;
3. buat `InferenceRuntime`;
4. tulis heartbeat status;
5. polling `control.txt`;
6. rising edge Start -> `runtime.start()`;
7. falling edge Stop -> close frame writer, `runtime.stop()`, hapus result;
8. baca latest frame;
9. tolak frame sebelum session atau sequence duplicate;
10. rate-limit ke target FPS;
11. `runtime.infer_frame()`;
12. serialize result atomic;
13. update metrics/status;
14. signal/error -> `runtime.shutdown()`.

### Registration writer disconnect

Pada Start, worker membuat `RegistrationFrameWriter`, tetapi beberapa baris kemudian `frame_writer = None`. Akibatnya submit tidak terjadi. Selain itu writer menarget `frames/registration`, sedangkan finalizer mencari `frames/processed`.

Status: **OPEN**. Finalizer saat ini umumnya jatuh ke identity transform. Jangan menganggap cross-frame visual registration aktif.

## 9. `InferenceRuntime`

### Lifecycle

| Method | Peran |
|---|---|
| `initialize()` | load detector dan segmenter sekali per worker |
| `warmup()` | dummy inference sebelum RUNNING |
| `start()` | buat session writer dan transition state |
| `infer_frame()` | entry point satu frame |
| `process_frame()` | detector/segmenter/cache/storage/overlay/metrics |
| `stop()` | close session lalu run finalizer |
| `fail()` | record structured error dan close error session |
| `shutdown()` | stop dan release model/CUDA resources |

Model object bertahan lintas Start/Stop; session writer dibuat ulang setiap Start.

### Inference flow - satu frame

```text
FrameEnvelope BGR
  -> detector interval check
  -> tiled/full detector prediction
  -> segmenter interval check
  -> reuse cached segmentation jika tidak due
  -> FrameResult + metrics
  -> SessionWriter.append(detections.jsonl)
  -> optional sample/snapshot
  -> select_overlay_detections
  -> worker.serialize_result
```

Detector baseline setiap frame; segmenter setiap 5 frame; runtime dapat reuse segmentation terakhir.

## 10. `tensorrt_backend.py`

### Shared infrastructure

- `CudaRuntime`: thin ctypes wrapper CUDA runtime API.
- `BindingBuffer`: pinned host + device allocation per binding.
- buffer/stream dibuat sekali dan digunakan ulang.
- `normalize_binding_metadata()` dan `validate_binding_contract()` menolak binding yang tidak sesuai.

### `DirectTensorRTModel`

- task wajib `detect` dan device `cuda:0`;
- engine di-hash read-only lalu deserialize;
- expected binding berasal dari audited config;
- preprocess: letterbox, BGR -> RGB, CHW, float/scaling;
- execute: H2D -> `execute_async_v2` -> D2H -> synchronize;
- decode: raw YOLOv8 `xywh + class scores`, confidence filter, class-aware NMS, reverse letterbox.

### `DirectTensorRTSegmenterModel`

- task wajib `segment` dan dua output;
- output detection memuat bbox, class, dan 32 mask coefficients;
- output prototype didecode dengan sigmoid;
- mask di-crop/resize/reverse-letterbox;
- union mask, morphology, contour filtering/simplification dilakukan pada CPU.

Failure model dikembalikan sebagai warning; fatal warnings tertentu membuat runtime masuk ERROR.

## 11. Session dan Storage

`SessionWriter` membuat session di bawah `runtime.root` dengan ID timestamp/unique value. Layout konseptual:

```text
runtime/
`-- sessions/<session-id>/
    |-- session.json
    |-- session_summary.json
    |-- detections.jsonl
    |-- logs/
    |-- frames/
    |-- snapshots/
    `-- results/
        |-- local/
        |-- geographic/
        |-- shp/
        |-- previews/
        `-- output_manifest.json
```

`RotatingJsonlWriter` membatasi ukuran JSONL dan backup. Storage config juga menetapkan max session bytes, minimum free bytes, log rotation, snapshot, dan preview policy.

`FrameResult` membawa detection, segmentation, gap candidate, telemetry, latency breakdown, overlay, dan warnings. `Telemetry.to_dict()` menjaga null untuk field yang tidak valid.

## 12. Finalization Flow - Stop AI

```text
runtime.stop()
  -> close SessionWriter
  -> finalize_session(session_dir, config)
  -> read detections.jsonl
  -> load registration frames jika ada
  -> register frame sequence
  -> fuse_detections
  -> estimate row orientation/spacing
  -> analyze_gaps
  -> local GeoJSON + SHP + preview
  -> finalization_summary.json
  -> output_manifest.json
```

### Frame registration

`register_frame_pair()` memakai SIFT, mutual ratio match, dan RANSAC partial affine. `register_frame_pair_klt()` tersedia sebagai helper tetapi tidak dipanggil oleh `register_frame_sequence()` saat ini. Transform yang tidak tersedia diganti identity.

### Cross-frame fusion

`fuse_detections()`:

- kelompokkan detection per frame;
- map center bbox melalui frame transform;
- greedy one-to-one association memakai `SimpleKDTree` dan radius config;
- aggregate track dengan median local coordinate/confidence;
- filter minimum observation.

### Gap analysis

`analyze_gaps()`:

1. estimasi dominant planting orientation;
2. transform plant ke `(along, across)` row frame;
3. cluster row berdasarkan across distance;
4. estimasi robust spacing;
5. identifikasi expected slot tanpa nearby plant;
6. back-project expected slot ke local coordinates.

Fix v27:

```text
local_x = cx + along*cos(theta) - across*sin(theta)
local_y = cy + along*sin(theta) + across*cos(theta)
```

Helper `_row_coords_to_local()` memperbaiki `V26-SOFTWARE-BLOCKER-001`.

### Output limitation

Finalizer menulis local coordinates dan memberi label `LOCAL_PIXEL`. File di folder `geographic` masih mengandung local coordinate dengan property `mapping=local_pixel_only`. SHP saat ini diberi WGS84 `.prj` walaupun point masih local pixel.

Status: **PARTIALLY RESOLVED / DO NOT USE AS TRUE GIS** sampai registration + local-to-world RTK mapping tervalidasi.

`geospatial_v2.py` menyediakan RTK classification, WGS84/UTM conversion, dan similarity fitting, tetapi belum dipanggil oleh finalizer canonical.

## 13. Di Mana Mengubah Apa

| Tujuan perubahan | File utama | Wajib cek juga |
|---|---|---|
| camera/source/pixel format | `main.cpp`, `config/app.yaml` | PSDK liveview compatibility |
| IPC/header | `main.cpp`, `worker.py` | static size/version tests |
| inference cadence/cache | `config/live.yaml`, `runtime.py` | latency/session semantics |
| detector decode/NMS | `tensorrt_backend.py`, `tiling.py` | parity tests/expected bindings |
| segmenter mask | `tensorrt_backend.py` | morphology/contour config |
| Pilot box selection | `overlay.py`, `worker.py`, `main.cpp` | 0..10000 mapping/stale handling |
| session schema | `schema.py`, `storage.py` | finalizer/evidence consumers |
| plant fusion/gap | `finalizer.py`, `spatial.py` | v27 T01/T02/T03 fixtures |
| true GIS/RTK | `geospatial_v2.py`, `finalizer.py` | field control points/CRS |
| startup owner | `dpk_launcher.sh`, `main.cpp` | process supervision/cleanup |

## 14. Known Source Risks Sebelum Build v27

- DPK launcher dan native sama-sama dapat start worker.
- `DjiCore_ApplicationStart()` ordering perlu dicocokkan lagi dengan PSDK 3.16 guidance.
- registration writer langsung di-reset ke `None`.
- writer/finalizer memakai directory berbeda.
- geographic output belum true geographic.
- `debugVersion=25`, DPK template `00.01.00.00`, dan source v27 tidak selaras.
- engine builder/runtime artifact format/naming perlu reconciliation.
- v27 belum pernah build/package/deploy/hardware runtime validation.

## Referensi Resmi

Terakhir diverifikasi: **2026-08-20**.

- [DJI Payload SDK API Reference](https://developer.dji.com/doc/payload-sdk-api-reference/en/)
- [DJI Core API](https://developer.dji.com/doc/payload-sdk-api-reference/en/core/dji-core.html)
- [PSDK Liveview](https://developer.dji.com/doc/payload-sdk-tutorial/en/function-overview/advanced-function/liveview.html)
- [PSDK Data Subscription](https://developer.dji.com/doc/payload-sdk-tutorial/en/function-overview/basic-function/fc-subscription.html)
- [PSDK Custom Widget](https://developer.dji.com/doc/payload-sdk-tutorial/en/function-overview/basic-function/custom-widget.html)
- [PSDK Gimbal Management](https://developer.dji.com/doc/payload-sdk-tutorial/en/function-overview/advanced-function/gimbal-management.html)
- [PSDK Data Transmission](https://developer.dji.com/doc/payload-sdk-tutorial/en/function-overview/basic-function/data-transmission.html)
