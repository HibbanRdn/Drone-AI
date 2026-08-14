# AI Pipeline

Dokumen ini menjelaskan model, inference, post-processing, dan finalizer berdasarkan source canonical v27.

## Lineage Model

```text
training artifact
  -> PyTorch checkpoint (.pt)
  -> ONNX export
  -> TensorRT FP16 engine pada Manifold 3
  -> runtime inference via DirectTensorRTModel / DirectTensorRTSegmenterModel
```

Repository ini menyimpan `.pt` dan `.onnx` melalui Git LFS. TensorRT `.engine` tidak disimpan sebagai binary karena artifact tersebut target-specific terhadap GPU, CUDA, TensorRT, dan arsitektur.

## Engine Runtime Manifold

Path berikut adalah path pada Manifold:

| Peran | Engine | Input | Hash snapshot |
|---|---|---|---|
| Detector | `plant_center_manual_v1_b0_best_fp16.engine` | 1024 | `e5594f9e945791e5ef201b34ef429194a3338fa61ba576f35a2e63044480c600` |
| Segmenter | `plot_segmenter_b4_selected_best_fp16.engine` | 1280 | `4d2684bdaef13a8fd678d66c9803970027d6c0afd00276125f9d95b463278605` |

Config canonical:

```text
config/live.yaml
```

Config tersebut menunjuk ke engine external:

```text
/home/dji/gap_plot_ai_assets/models/engine/
```

## Detector

Tujuan detector:

```text
plant-center detection dan plant point extraction
```

Artifact:

| Jenis | Path |
|---|---|
| PyTorch checkpoint | `models/source/plant_center_manual_v1_b0_best.pt` |
| ONNX export | `models/onnx/plant_center_manual_v1_b0_best.onnx` |
| TensorRT engine | external pada Manifold |

Runtime class:

```text
src/gap_plot_ai/tensorrt_backend.py::DirectTensorRTModel
```

Flow preprocessing dan postprocessing:

1. Worker menerima frame BGR dari OpenCV.
2. Frame dikonversi BGR -> RGB.
3. Frame di-letterbox ke input 1024.
4. Data diubah ke CHW float dan diskalakan 0..1.
5. Tensor dipindah host-to-device.
6. TensorRT context dieksekusi.
7. Output dipindah device-to-host.
8. Output YOLOv8-style `xywh + class_scores` di-decode.
9. Koordinat dikembalikan dari letterbox ke ukuran frame asal.
10. Confidence threshold dan OpenCV NMS diterapkan.

Jika `models.detector.tiled` aktif, `InferenceRuntime` memakai `predict_tiled_detector()` dari `src/gap_plot_ai/tiling.py`. Frame dipecah menjadi tile 1024 px dengan overlap, prediksi tile dikonversi kembali ke koordinat global, lalu digabung dengan global NMS.

## Segmenter

Tujuan segmenter:

```text
plantable-area / plot ROI segmentation
```

Artifact:

| Jenis | Path |
|---|---|
| PyTorch checkpoint | `models/source/plot_segmenter_b4_selected_best.pt` |
| ONNX export | `models/onnx/plot_segmenter_b4_selected_best.onnx` |
| TensorRT engine | external pada Manifold |

Runtime class:

```text
src/gap_plot_ai/tensorrt_backend.py::DirectTensorRTSegmenterModel
```

Flow:

1. Frame BGR diterima dari worker.
2. Frame di-letterbox ke input 1280.
3. TensorRT segmenter dieksekusi.
4. Output YOLOv8-seg dan prototype mask di-decode.
5. Confidence threshold dan NMS diterapkan.
6. Mask coefficient dikombinasikan dengan prototype.
7. Sigmoid diterapkan ke mask logits.
8. Mask dikembalikan ke ukuran frame asal.
9. Morphology open/close diterapkan.
10. Mask dikonversi menjadi contour.

Segmenter aktif pada config v27. Runtime dapat memakai ulang segmentation terakhir melalui `runtime.reuse_last_segmentation` dan interval `models.segmenter.interval_frames`.

## Scheduling Dan Cache

Config penting:

| Config | Makna |
|---|---|
| `live.target_inference_fps` | rate-limit worker |
| `models.detector.interval_frames` | interval detector |
| `models.segmenter.interval_frames` | interval segmenter |
| `runtime.reuse_last_segmentation` | memakai cache segmentation antar frame |
| `overlay.max_objects` | batas object untuk Pilot overlay |
| `overlay.stale_result_timeout_ms` | timeout metadata overlay |

Native C++ hanya mempertahankan latest frame. Jika frame masuk lebih cepat dari inference, pending frame lama diganti, bukan menumpuk queue besar.

## Cross-Frame Processing

Output per frame ditulis melalui `SessionWriter`. Ketika Stop AI, `InferenceRuntime.stop()` memanggil `finalize_session()` untuk menggabungkan detection antar frame menjadi unique plant dan gap result.

Known issue: capture registration keyframe perlu direview karena `RegistrationFrameWriter` pada v27 dibuat lalu di-reset ke `None` di `worker.py`.

## Finalizer Dan Gap Analysis

File:

```text
src/gap_plot_ai/finalizer.py
```

`finalize_session()` melakukan:

1. load detection record;
2. load processed frame bila tersedia;
3. registration transform bila data tersedia;
4. fusion detection menjadi unique plant;
5. estimasi orientasi dan spacing baris;
6. `analyze_gaps()`;
7. export output.

`analyze_gaps()` memproyeksikan plant ke row-coordinate frame:

```text
along  = (x - cx) * cos(theta) + (y - cy) * sin(theta)
across = -(x - cx) * sin(theta) + (y - cy) * cos(theta)
```

Fix v27 memakai inverse:

```text
local_x = cx + along * cos(theta) - across * sin(theta)
local_y = cy + along * sin(theta) + across * cos(theta)
```

Itu adalah fix untuk `V26-SOFTWARE-BLOCKER-001`.

## Output

Output session yang diharapkan:

```text
session.json
session_summary.json
detections.jsonl
finalization_summary.json
results/output_manifest.json
results/local/*.geojson
results/geographic/*.geojson
results/shp/*
results/previews/*
```

## Status Geospatial

`src/gap_plot_ai/geospatial_v2.py` menyediakan utilitas WGS84/UTM dan transform. Namun evidence snapshot menunjukkan output finalizer tertentu masih diberi label local-pixel mapping. Karena itu, true GIS/geographic export harus dianggap partially resolved sampai divalidasi end-to-end dengan data RTK/field.

## Cara Memperoleh Engine

Developer berikutnya dapat menggunakan lineage yang ada:

```text
models/source/*.pt
models/onnx/*.onnx
models/model_manifest.json
config/live.yaml
```

Engine TensorRT harus dibuat atau divalidasi pada Manifold 3 atau environment identik:

```text
Linux aarch64
CUDA 11.4
TensorRT 8.5.2.2
DJI Manifold 3 / NVIDIA Orin NX
```

Jangan melakukan conversion engine di macOS/Windows/x86/unrelated GPU untuk kemudian dianggap valid pada Manifold.
