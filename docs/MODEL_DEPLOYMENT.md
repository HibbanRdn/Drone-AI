# Model Development dan Deployment

Dokumen ini menjelaskan lifecycle model project dari checkpoint training sampai TensorRT engine pada Manifold 3. Command berasal dari script canonical repository. Tidak ada conversion atau inference yang dijalankan saat dokumentasi ini disusun.

## 1. Flow Artifact

```text
dataset + training config
    -> best.pt
    -> audited ONNX export
    -> TensorRT FP16 build pada Manifold 3
    -> read-only engine inspection/parity
    -> protected engine directory
    -> config/live.yaml
    -> runtime smoke test
    -> field validation
```

Detector dan segmenter mengikuti flow yang sama tetapi task, input size, output tensor, dan postprocessing berbeda.

## 2. Arti Format

### 2.1 `.pt`

Checkpoint PyTorch/Ultralytics hasil training. Format ini menyimpan model dan metadata task/class. Ia fleksibel untuk development, inspection, dan export, tetapi runtime onboard project v27 tidak menjalankan `.pt`; runtime live memakai direct TensorRT.

### 2.2 `.onnx`

ONNX adalah graph deployment intermediate yang memisahkan training framework dari builder TensorRT. Project memakainya untuk:

- pemeriksaan graph dengan `onnx.checker`;
- parity PT/ONNX;
- input builder TensorRT;
- provenance yang lebih portable daripada engine.

### 2.3 `.engine`

Serialized TensorRT plan berisi graph teroptimasi dan pilihan CUDA tactic/kernel. Engine dipakai karena targetnya low-latency inference pada GPU Manifold, bukan training.

```text
.pt              ONNX                  TensorRT engine
fleksibel   ->    graph portable   ->  runtime target-specific
training/dev      deployment bridge    optimized inference
```

## 3. Compatibility dan Security Engine

> Status: NVIDIA OFFICIAL REQUIREMENT + PROJECT POLICY.

Secara default TensorRT engine hanya dijamin pada OS/platform, CPU architecture, GPU/compute capability, dan versi TensorRT yang sama dengan environment build. JetPack saat ini tidak mendukung hardware compatibility mode yang dijelaskan TensorRT terbaru. Karena project ini memakai TensorRT 8.5.2.2 pada JetPack/CUDA lama, jangan menganggap fitur compatibility TensorRT terbaru otomatis tersedia.

Policy project:

```text
Build engine pada target Manifold 3 atau environment yang benar-benar identik.
Jangan deserialize engine dari sumber yang tidak dipercaya.
Verifikasi SHA-256, size, TensorRT version, dan binding sebelum aktivasi.
```

Engine harus dibuat ulang bila:

- model `.pt`/ONNX berubah;
- input size, task, class, precision, dynamic shape, atau opset berubah;
- Manifold/GPU diganti;
- firmware mengubah CUDA, TensorRT, JetPack, atau plugin;
- engine gagal deserialize;
- binding tidak sesuai contract;
- performance/tactic perlu dibangun ulang pada target baru.

NVIDIA memperlakukan deserialize engine sebagai trust boundary setara binary native. Hanya load engine yang dibangun sendiri atau diterima melalui jalur terautentikasi dan diverifikasi.

## 4. Artifact Baseline Project

| Peran | Source `.pt` | ONNX | Input | Task/class |
|---|---|---|---:|---|
| Detector | `models/source/plant_center_manual_v1_b0_best.pt` | `models/onnx/plant_center_manual_v1_b0_best.onnx` | `1024 x 1024` | detect / `plant` |
| Segmenter | `models/source/plot_segmenter_b4_selected_best.pt` | `models/onnx/plot_segmenter_b4_selected_best.onnx` | `1280 x 1280` | segment / `plantable_area` |

Observed external runtime engine:

```text
/home/dji/gap_plot_ai_assets/models/engine/plant_center_manual_v1_b0_best_fp16.engine
/home/dji/gap_plot_ai_assets/models/engine/plot_segmenter_b4_selected_best_fp16.engine
```

Hash/size artifact baseline ada di `models/model_manifest.json`. Engine tidak disimpan Git karena target-specific dan generated.

## 5. Source of Truth Model

| File | Fungsi |
|---|---|
| `models/model_manifest.json` | inventory, hash, size, lineage, runtime path |
| `models/configs/training_provenance.json` | provenance training run |
| `models/configs/export.json` | metadata export historical |
| `models/configs/postprocess.json` | parameter postprocess |
| `config/app.yaml` | config canonical export/build dan contract runtime |
| `config/live.yaml` | override live Manifold dan external engine path |
| `src/gap_plot_ai/export_models.py` | export PT -> ONNX |
| `src/gap_plot_ai/build_engine.py` | ONNX -> TensorRT FP16 pada aarch64 |
| `src/gap_plot_ai/tensorrt_backend.py` | deserialize, binding validation, inference, decode |

Jangan memperbarui satu file saja. Model baru harus mengubah source artifact, hash, config, manifest, evidence, dan runtime path sebagai satu change set.

## 6. Baseline Preflight Setelah Clone

Jalankan pada development host dari repository root:

```bash
git lfs install
git lfs pull
python3 scripts/model_preflight.py
```

`model_preflight.py` memeriksa:

- artifact bukan pointer LFS;
- size dan SHA-256 sesuai manifest;
- `.gitattributes` memakai LFS;
- config JSON dapat dibaca;
- `config/app.yaml` menunjuk artifact yang dimanifestasikan.

Expected output:

```text
model_preflight_ok artifacts=4 ...
```

## 7. Menyiapkan `best.pt` Baru

### 7.1 Preserve baseline

Sebelum mengganti model:

1. catat Git commit;
2. simpan metric training/validation;
3. catat dataset version dan split;
4. catat Ultralytics/PyTorch version;
5. simpan hash model lama dan baru;
6. jangan menghapus known-good engine dari Manifold sebelum candidate lulus.

### 7.2 Detector

Untuk detector baru:

- task harus `detect`;
- class map harus sesuai config (`0: plant`) atau perubahan contract harus direview;
- input target baseline 1024;
- output harus cocok dengan decoder `ultralytics_v8_xywh_class_scores`;
- embedded NMS tetap `false` karena runtime melakukan NMS.

### 7.3 Segmenter

Untuk segmenter baru:

- task harus `segment`;
- class map baseline `0: plantable_area`;
- input target baseline 1280;
- output harus berupa detection tensor + mask prototype yang dapat didecode backend;
- embedded NMS tetap `false`.

### 7.4 Update registry

1. Letakkan checkpoint pada `models/source/` dengan nama versioned yang jelas.
2. Track binary model melalui Git LFS.
3. Hitung SHA-256:

```bash
shasum -a 256 models/source/<new-model>.pt
```

4. Update `config/app.yaml`: `path`, `source_sha256`, task, class names, image size, export options, dan TensorRT contract.
5. Update `models/model_manifest.json` dan training provenance.
6. Jangan menyalin hash dari model lama.

## 8. Export PT ke ONNX

### 8.1 Prerequisite host

Host environment repository mendukung Python 3.10-3.12 untuk dependency export. Setup:

```bash
./scripts/bootstrap_dev.sh
```

Script membuat `.venv`, memasang extras `host,export,test,postprocess`, dan memvalidasi config. Dependency host bukan dependency Manifold.

### 8.2 Command project

```bash
./scripts/export_models.sh config/app.yaml
```

Script memanggil entry point:

```text
gap-plot-ai-export --config config/app.yaml
```

`export_models.py` untuk setiap model:

1. membaca path checkpoint;
2. mencocokkan SHA-256 dengan config;
3. load `YOLO(..., task=...)`;
4. mencocokkan task dan class names;
5. memanggil Ultralytics export dengan parameter dari config;
6. melakukan atomic replace ONNX target;
7. menjalankan `onnx.checker.check_model()`;
8. menulis `runtime/reports/model_export_report.json`.

Parameter baseline:

| Parameter | Detector | Segmenter |
|---|---:|---:|
| `imgsz` | 1024 | 1280 |
| `batch` | 1 | 1 |
| `opset` | 17 | 17 |
| `dynamic` | false | false |
| `simplify` | false | false |
| export FP16 | false | false |
| INT8 | false | false |
| embedded NMS | false | false |
| device export | CPU | CPU |

Expected output:

- ONNX pada path `onnx_path` masing-masing;
- report export JSON;
- `onnx.checker` PASS.

Common failure:

- hash mismatch: config/manifest belum diperbarui atau file salah;
- task/class mismatch: checkpoint tidak sesuai contract;
- operator/opset error: cek versi Ultralytics/ONNX dan TensorRT target;
- LFS pointer: jalankan `git lfs pull`;
- disk penuh: script memerlukan ruang bebas minimum.

## 9. Validasi ONNX

Minimal gate:

```bash
python3 scripts/model_preflight.py
./scripts/parity.sh <video-or-frame-source>
```

Gunakan argumen `scripts/parity.sh` sesuai `--help` pada versi repository. Parity membandingkan backend portable terhadap baseline berdasarkan tolerance di `config/app.yaml`.

Jangan lanjut ke engine jika:

- `onnx.checker` gagal;
- input/output shape tidak sesuai;
- parity detector/segmenter gagal;
- class/task berubah tanpa perubahan decoder.

## 10. Transfer ke Manifold

Transfer source package terverifikasi atau repository checkout ke Manifold. Jangan transfer `.venv` host. Pastikan ONNX bukan LFS pointer:

```bash
file models/onnx/*.onnx
python3 scripts/model_preflight.py
```

Manifold venv harus memakai package Python 3.8/aarch64 yang kompatibel dengan JetPack. Workflow offline project membuat venv `--system-site-packages` dan memasang wheel target; lihat `BUILD_DEPLOYMENT.md` dan `MANIFOLD_FIRST_RUN.md`.

## 11. Build TensorRT Engine pada Manifold

### 11.1 Environment gate

Jalankan pada **Manifold 3 Linux aarch64**, bukan macOS/Windows/x86:

```bash
uname -m
python3 -c 'import tensorrt as trt; print(trt.__version__)'
python3 -c 'import torch; print(torch.__version__)'
```

Lalu dari application source:

```bash
export GAP_PLOT_AI_APP_ROOT=<application-source-root>
export GAP_PLOT_AI_CONFIG=<application-source-root>/config/app.yaml
./scripts/build_engine.sh
```

Optional workspace:

```bash
GAP_PLOT_AI_TRT_WORKSPACE_GIB=2 ./scripts/build_engine.sh
```

`build_engine.sh` menolak non-Linux/non-aarch64, memeriksa venv/runtime/disk, lalu menjalankan:

```text
python -m gap_plot_ai.build_engine --config ... --workspace-gib ...
```

### 11.2 Perilaku builder

`build_engine.py`:

- memeriksa metadata ONNX `task`, `imgsz`, `names`, `args`;
- hanya mengizinkan FP16;
- memanggil Ultralytics `onnx2engine` dengan shape statis batch 1;
- menulis engine dengan metadata Ultralytics;
- mengekstrak payload TensorRT murni ke sibling `*.raw.engine`;
- menulis `runtime/reports/tensorrt_build_report.json`.

### 11.3 Gate penting sebelum production path

Ada reconciliation yang masih **OPEN**:

- `config/app.yaml` menghasilkan nama engine development seperti `*_tensorrt-8.5.2_cuda-11.4_aarch64_fp16.engine`;
- `config/live.yaml` menunjuk nama observed runtime `*_best_fp16.engine`;
- builder membuat engine ber-metadata dan `*.raw.engine`;
- direct backend memanggil `deserialize_cuda_engine()` pada file yang diberikan.

Karena itu jangan langsung rename/copy hasil build ke protected runtime path. Tentukan melalui inspection/parity apakah direct backend harus menerima engine utama atau `.raw.engine`, lalu dokumentasikan keputusan dan hash. Ini belum boleh dianggap resolved hanya karena build command selesai.

## 12. Inspect dan Validasi Engine

Read-only inspection script:

```bash
python3 scripts/inspect_tensorrt_engine.py <engine-path> \
  --output runtime/diagnostics/<model>-engine.json
```

Protected detector helper:

```bash
GAP_PLOT_AI_DETECTOR_ENGINE_PATH=<engine-path> \
  ./scripts/validate_engine_readonly.sh
```

Script memeriksa deserialization, binding, TensorRT version, hash, dan memastikan engine tidak berubah selama validation. `trtexec --skipInference` dipakai jika tersedia.

Validasi kedua model secara terpisah. Untuk segmenter, pastikan dua output (detection + prototypes) dan shape sesuai decoder; jangan hanya menguji bahwa file dapat dibuka.

Gate sebelum deploy:

- [ ] engine deserialize pada target runtime;
- [ ] TensorRT/CUDA/platform tercatat;
- [ ] input binding batch 1 dan image size benar;
- [ ] detector output cocok `xywh + class scores`;
- [ ] segmenter punya detection + prototype output;
- [ ] PT/ONNX/engine parity lulus;
- [ ] hash dan size dicatat;
- [ ] engine source dipercaya.

## 13. Activate Candidate Engine

1. Copy candidate ke staging directory, bukan menimpa known-good.
2. Verifikasi SHA-256 setelah transfer.
3. Gunakan nama versioned yang menyertakan model/version/precision.
4. Update `models/model_manifest.json`.
5. Update `config/live.yaml` ke exact absolute Manifold path.
6. Jalankan static/engine preflight.
7. Stop app sebelum mengganti active path.
8. Start app dan lakukan smoke test.
9. Simpan engine lama untuk rollback.

Target protected directory project:

```text
/home/dji/gap_plot_ai_assets/models/engine
```

DPK saat ini sengaja tidak memuat engine. Engine lifecycle dikelola terpisah dari application package.

## 14. Runtime Detector Flow

Config: `config/app.yaml` + override `config/live.yaml`.

1. `_resolve_engine_path()` menentukan exact engine.
2. `DirectTensorRTModel` memverifikasi file dan device `cuda:0`.
3. TensorRT runtime deserialize engine dan membuat execution context.
4. Binding dibandingkan dengan `expected_bindings`.
5. Frame BGR diproses dengan letterbox, BGR -> RGB, CHW, skala 0..1.
6. Input disalin ke pinned host/device buffer.
7. `execute_async_v2()` menjalankan inference.
8. Output didecode sebagai `xywh + class scores`.
9. Confidence filter dan class-aware OpenCV NMS diterapkan.
10. Bounding box di-reverse-letterbox ke source frame.

Detector dapat berjalan tiled melalui `predict_tiled_detector()`:

- tile 1024;
- overlap 128;
- detection tile dikembalikan ke global frame;
- global NMS menghapus duplicate antar tile;
- center suppression baseline dimatikan.

## 15. Runtime Segmenter Flow

1. Engine segmenter deserialize dan menyediakan dua output.
2. Frame di-letterbox ke 1280.
3. Output detection menentukan box/class/confidence/mask coefficients.
4. Prototype mask digabung dengan mask coefficients dan sigmoid.
5. NMS memilih instance.
6. Mask di-crop ke box dan di-resize/reverse-letterbox ke source frame.
7. Union mask dibuat untuk plantable area.
8. Morphology open/close memakai kernel config.
9. Contour kecil dibuang, contour disederhanakan, dan point count dibatasi.

Segmenter dijalankan setiap 5 frame pada baseline dan hasil terakhir dapat di-cache melalui `runtime.reuse_last_segmentation`.

## 16. Hubungan Model ke Finalizer

```text
detector per frame -> plant observations
segmenter per interval -> plantable-area mask/contour
session storage -> detections.jsonl
registration/fusion -> unique plant
row analysis -> expected slots -> gaps
back-projection -> local output
```

Fix v27 `V26-SOFTWARE-BLOCKER-001` berada setelah inference: `_row_coords_to_local()` mengembalikan expected gap slot dari row frame ke local coordinates. Ia tidak mengubah TensorRT model.

Current limitation: registration frame writer belum tersambung benar dan geographic output masih local-pixel mapping. Model baru tidak boleh dipakai untuk menyimpulkan GIS accuracy sebelum issue tersebut selesai.

## 17. Smoke Test dan Field Test

### Smoke test model

- [ ] engine load tanpa deserialize error;
- [ ] warmup lulus;
- [ ] detector menghasilkan output dengan class benar;
- [ ] segmenter menghasilkan mask/contour masuk akal;
- [ ] latency/FPS dicatat;
- [ ] tidak ada buffer/binding warning;
- [ ] session JSONL terbentuk;
- [ ] Pilot overlay menerima box;
- [ ] stop session menjalankan finalizer.

### Field validation

- [ ] dataset lapangan representatif;
- [ ] detector precision/recall dihitung;
- [ ] segmentation quality divalidasi;
- [ ] unique plant fusion dibanding ground truth;
- [ ] gap precision/recall dan coordinate error dihitung;
- [ ] RTK/geospatial output dibanding control point;
- [ ] long-run thermal/memory/stability diuji.

v27 pada handover belum melalui smoke/field validation hardware penuh.

## 18. Rollback

Jika candidate gagal:

1. stop app;
2. kembalikan `config/live.yaml`/deployed config ke known-good engine path;
3. verifikasi hash known-good dari manifest/evidence;
4. start app;
5. ulangi smoke test;
6. simpan candidate dan error report, jangan overwrite evidence.

Rollback model tidak mengubah fakta bahwa latest deployed application saat handover masih v26.

## Referensi Resmi

Terakhir diverifikasi: **2026-08-20**.

- [NVIDIA TensorRT - How TensorRT Works](https://docs.nvidia.com/deeplearning/tensorrt/latest/architecture/how-trt-works.html)
- [NVIDIA TensorRT - Engine Compatibility](https://docs.nvidia.com/deeplearning/tensorrt/latest/inference-library/engine-compatibility.html)
- [NVIDIA TensorRT - Support Matrix](https://docs.nvidia.com/deeplearning/tensorrt/latest/getting-started/support-matrix.html)
- [NVIDIA TensorRT 8.5.2 archive](https://docs.nvidia.com/deeplearning/tensorrt/archives/tensorrt-852/)
- [Ultralytics ONNX integration](https://docs.ultralytics.com/integrations/onnx/)
- [Ultralytics export mode](https://docs.ultralytics.com/modes/export/)
