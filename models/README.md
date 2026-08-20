# Model Artifacts

Folder ini menyimpan artifact model portable dan provenance model. TensorRT `.engine` tidak dicommit karena bersifat target-specific.

Setelah clone:

```bash
git lfs install
git lfs pull
python3 scripts/model_preflight.py
```

Panduan lengkap ada di [Model Deployment](../docs/MODEL_DEPLOYMENT.md). Jangan melakukan conversion sebelum membaca compatibility dan artifact-format gate pada dokumen tersebut.

## Struktur

| Folder/file | Isi |
|---|---|
| `source/` | PyTorch checkpoint detector dan segmenter |
| `onnx/` | ONNX export FP32, batch 1, opset 17, tanpa embedded NMS |
| `configs/` | provenance training/export/postprocess |
| `engine/` | reserved untuk engine yang sengaja ditrack; saat ini tidak berisi engine runtime |
| `model_manifest.json` | checksum, shape, lineage, dan metadata engine Manifold |

## Engine Runtime Manifold

Runtime v26/v27 pada snapshot memakai engine external pada Manifold:

```text
/home/dji/gap_plot_ai_assets/models/engine/plant_center_manual_v1_b0_best_fp16.engine
/home/dji/gap_plot_ai_assets/models/engine/plot_segmenter_b4_selected_best_fp16.engine
```

Binary engine tersebut tidak dimasukkan ke Git. Path, ukuran, dan hash dicatat di `model_manifest.json` dan `docs/AI_PIPELINE.md`.

## Lineage

```text
.pt checkpoint
  -> ONNX export
  -> TensorRT FP16 engine pada Manifold 3 atau environment identik
  -> config/live.yaml
```

Export ONNX portable dilakukan melalui `scripts/export_models.py`; build engine target dilakukan melalui `scripts/build_engine.py` pada Manifold. Script project mengekspor ONNX FP32, batch 1, opset 17, static shape, tanpa embedded NMS, kemudian membangun TensorRT FP16 static engine.

Jangan membuat engine di macOS, Windows, x86, atau GPU lain lalu menganggapnya valid untuk Manifold. Gunakan Manifold 3 atau environment Linux aarch64 yang kompatibel. Setelah build, verifikasi raw/deserializable artifact, binding, hash, dan hasil parity sebelum mengaktifkannya di `config/live.yaml`.
