# Liveview pipeline PSDK 3.16.0

## Kontrak API resmi

Implementasi dicocokkan langsung dengan
`psdk_lib/include/dji_liveview.h` dan sample
`dji_liveview_object_detection.cpp` pada tag `3.16.0`.

Urutan lifecycle:

1. platform handler dan `DjiCore_Init` berhasil;
2. `DjiLiveview_Init()`;
3. register label `plant` dengan
   `DjiLiveview_RegUserAiTargetLableList()`;
4. `DjiLiveview_StartImageStream(DJI_LIVEVIEW_CAMERA_POSITION_NO_1,
   DJI_LIVEVIEW_CAMERA_SOURCE_M4E_VIS, PIXFMT_RGB_PACKED, callback)`;
5. callback memvalidasi `T_DjiLiveviewImageInfo`, menyalin frame terbaru, lalu
   segera return;
6. thread worker terpisah menjalankan tiling/inference dan
   `DjiLiveview_SendAiMetaToPilot()`;
7. stop stream, unregister label, `DjiLiveview_Deinit()`;
8. callback setelah flag shutdown diabaikan.

`DjiLiveview_StartImageStream` dan `StopImageStream` secara eksplisit diberi
catatan “support on DJI manifold3” pada header. Konfigurasi memakai visual
source M4E (`M4E_VIS=1`), bukan asumsi H20/M3 atau 4K recording source.

## Input dan output adalah jalur berbeda

Input inference adalah decoded RGB dari `StartImageStream`. Ini bukan video
hasil AI dan tidak mengambil alih recording. H.264 input dari
`StartH264Stream` hanya fallback masa depan; source saat ini menolaknya dengan
error capability yang jelas karena decoder FFmpeg target belum divalidasi.

Output default adalah metadata box ke normal Pilot liveview melalui
`DjiLiveview_SendAiMetaToPilot`. Header resmi mendefinisikan koordinat sebagai
1/10.000 lebar/tinggi layar dan `boxCount` sebagai `uint8_t`; implementation
membatasi 255 objek secara ABI dan mencatat total/sent/truncated. Tampilan dan
batas operasional tetap harus dibuktikan pada firmware Pilot 2 target.

Rendered video adalah jalur ketiga: `DjiLiveview_EncodeAFrameToH264`, encoder
callback, lalu `DjiPayloadCamera_SendVideoStream`. Jalur itu hanya diperlukan
untuk payload-camera/virtual stream, bukan untuk metadata box pada liveview
normal, dan default-nya nonaktif.

## Latensi rendah dan reconnect

- buffer hanya menyimpan satu frame dengan sequence dan monotonic timestamp;
- frame pending lama diganti, tidak ada antrean 30 FPS;
- callback tidak melakukan inference, drawing, JSON, encoding, atau I/O log;
- worker/context TensorRT persisten melewati Start/Stop session;
- stream timeout memindahkan state ke ERROR dan reconnect memakai stop/start
  terkontrol;
- Stop idempotent, thread di-join, metadata stale dihentikan setelah 1500 ms;
- Pixel format, width, height, frame ID, row stride turunan, monotonic/UTC
  timestamp dan drop count masuk session log.

## TensorRT direct runtime

Engine dibaca read-only dari `GAP_PLOT_AI_DETECTOR_ENGINE_PATH` bila di-set,
atau dari satu-satunya `.engine` di protected directory
`/home/dji/gap_plot_ai_assets/models/engine`. Source, DPK, dan paket offline
tidak membawa, menghapus, menimpa, atau build ulang engine. Runtime membuat
satu `trt.Runtime`, satu engine, satu execution context, satu CUDA stream,
serta pinned-host/device buffer yang dipakai ulang. Kontrak binding awal adalah:

```text
images   float [1, 3, 1024, 1024]
output0  float [1, 5, 21504]
decoder  Ultralytics v8 xywh + one class score
```

Kontrak ini berasal dari ONNX detector yang tersedia lokal. Engine Manifold
harus diinspeksi read-only; mismatch nama/shape memblokir Start. Preprocessing
adalah BGR→RGB, letterbox per tile, NCHW float32 0..1. Detector NMS per tile dan
global NMS memakai OpenCV native; center suppression tetap false. Parity
terhadap baseline TorchVision device adalah hardware gate, bukan klaim macOS.
