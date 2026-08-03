# Deployment artifact audit

Audit terakhir 3 Agustus 2026.

| Kapabilitas | Source/config/artifact repository | Status |
|---|---|---|
| Model export | src/gap_plot_ai/export_models.py, models/source, export config | tersedia |
| Video inference | src/gap_plot_ai/offline.py dan tools/b0_manual_v1_video_demo | tersedia |
| Plant-center/points | detector PT/ONNX, tiled inference, merge/suppression | tersedia |
| Plot/ROI segmentation | segmenter PT/ONNX, contour/mask code | tersedia |
| Tracking/unique count | registration + fusion tool, postprocess config | offline tersedia |
| Gap analysis | row reconstruction, expected/missing points, grouping | offline tersedia |
| ONNX | dua model checked-in dan exporter reproducible | checker/parity diuji |
| TensorRT | target config dan guarded build script | engine tidak ada di repo; benchmark device dilaporkan, source/engine belum tersinkron |
| PSDK frontend | src/psdk, CMake, widget config | source tersedia; device gate belum lulus |
| Dev-run | bootstrap, build, run, status/log scripts | tersedia |
| DPK | payload staging, validator, official tool invocation, dan hardware gates | flow siap; final device package diblokir |

Yang sengaja tidak masuk:

- credential dan environment file asli;
- virtualenv atau dependency terinstal;
- raw training dataset, raw flight video/SRT;
- runtime cache, log, report, IPC, preview, dan temporary inference output;
- engine dari Mac/Windows atau platform TensorRT yang berbeda.

Nilai yang belum diketahui atau belum tervalidasi:

- TensorRT engine checksum/size/parity/benchmark, karena engine wajib dibuat
  pada Manifold 3 atau target identik;
- versi firmware package untuk DPK ver_min/ver_max;
- readback packaging resmi seluruh dependency runtime Python target pada
  Manifold/Pilot 2;
- on-device PSDK liveview/widget, TensorRT parity, dan ground test.

Artifact model tidak dibuat ulang dari nilai rekaan. Checkpoint berasal dari
training artifacts resmi lokal; ONNX berasal dari exporter project dan
checksum-nya diverifikasi.
