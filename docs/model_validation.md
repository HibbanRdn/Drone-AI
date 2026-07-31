# Validasi model

Tanggal: 31 Juli 2026.

## Export ONNX

Export memakai PyTorch 2.7.1, Ultralytics 8.4.96, ONNX 1.22.0, opset 17,
FP32, fixed shape, tanpa embedded NMS. ONNX checker lulus.

| Model | Input/output | Ukuran | SHA-256 ONNX |
|---|---|---:|---|
| detector | `(1,3,1024,1024)` -> `(1,5,21504)` | 12.500.682 byte | `92a44b5f76a2ca8ceb385d0b4b2a34756cdcf7f3f1a10bea6ababdfdbe86b370` |
| segmenter | `(1,3,1280,1280)` -> `(1,37,33600)` dan `(1,32,320,320)` | 47.847.658 byte | `a2df02fe16cc0b57940fcd9669220eca493350ce40a59bd388e968958b5c7019` |

Report machine-readable:
`runtime/reports/model_export_report.json`.

## PyTorch vs ONNX

Frame video 0, 15, dan 30 diuji. Detector menjalankan seluruh tiled pipeline
dan global merge pada kedua backend, bukan hanya satu resize full-frame.
Parity lulus dengan toleransi config: count ±2, confidence absolut 0,03, bbox
absolut 4 px, dan union mask IoU minimum 0,98.

- Count detector PT/ONNX: 744/744, 766/766, dan 689/689.
- Count delta maksimum: 0.
- Unmatched detection maksimum: 0.
- Confidence error maksimum: 0,00000233.
- Bbox error maksimum: 0,000122 px.
- Mean bbox IoU minimum: 0,99999904.
- Segment union IoU: 0,99999965–1,0.

Mismatch awal berhasil ditelusuri ke rectangular inference PyTorch sementara
ONNX fixed-square. Runtime kini memakai `rect=False` secara eksplisit; threshold,
shape, class mapping, dan postprocessing tidak diubah untuk menutupi masalah.

Latency per frame pada MacBook Air M1, sehingga bukan benchmark Manifold:

| Model/backend | Frame 0 | Frame 15 | Frame 30 |
|---|---:|---:|---:|
| detector tiled PyTorch | 3.882,88 ms | 3.115,03 ms | 3.019,11 ms |
| detector tiled ONNX | 1.871,30 ms | 1.879,48 ms | 1.792,72 ms |
| segmenter PyTorch | 845,01 ms | 918,12 ms | 848,77 ms |
| segmenter ONNX | 664,26 ms | 654,50 ms | 630,68 ms |

Report: `runtime/reports/parity_pt_onnx.json`.
Parity ONNX ini memakai CPUExecutionProvider untuk mengisolasi perbedaan
serialisasi/backend dari provider CoreML.

## Offline integration tiled

Session final: `runtime/sessions/20260731_122318`.

- 6 frame 4K diproses, 6 JSONL records, 6 snapshot, preview 4K/6 frame.
- 15 tile detector/frame; 0 tile gagal; 0 warning.
- 761–776 plant bbox/frame; 4–6 contour/frame.
- Detector ONNX tiled warm: 1.448–1.524 ms/frame pada M1/CoreML.
- Segmenter ONNX: cold 2.734 ms; warm 229,93 ms pada M1/CoreML.
- Segmentasi interval 5; frame 1–4 memakai hasil contour terakhir dan metrics
  menandainya sebagai `segmenter_reused`.
- Telemetry offline bernilai `null`, tidak dibuat-buat.
- Session ditutup `completed`; storage cap tidak tercapai.
- JSONL aktif 1.236.338 byte, tanpa rotasi pada smoke test. Unit test
  memverifikasi rotasi berbatas dan pelaporan record lama yang terbuang saat
  cap benar-benar dilampaui.

Angka plant adalah deteksi frame saat ini, bukan jumlah tanaman unik.

## TensorRT

Inventory perangkat mengonfirmasi TensorRT 8.5.2 untuk CUDA 11.4. Error
`Model missing or format not recognized` pada report lama berasal dari
`trtexec --version`, yang bukan probe versi valid pada instalasi tersebut.
Package metadata/header digunakan untuk inventory versi.

Belum ada engine dan tidak ada benchmark TensorRT. ONNX tetap format
pertukaran. Engine harus dibangun pada Manifold 3 yang dituju atau environment
Linux aarch64, GPU, TensorRT 8.5.2, dan CUDA 11.4 yang identik; engine dari
Mac, Windows x86, GPU lain, atau versi TensorRT lain tidak dianggap portable.

Script `build_engine.sh` memakai TensorRT runtime melalui Ultralytics dan tidak
mewajibkan `nvcc`. Command berikut baru dijalankan setelah package Python
target dan ONNX tersedia serta inventory ulang lulus:

```bash
./scripts/check_disk_space.sh \
  --path . --min-mib 1024 --operation "TensorRT engine build"
./scripts/build_engine.sh
./scripts/benchmark_engine.sh
./scripts/parity.sh /path/to/representative_test.mp4 engine 0 15 30
```

INT8 tidak digunakan. Tidak ada engine yang dibangun atau inferensi perangkat
yang dijalankan dalam audit inventory ini.
