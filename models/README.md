# Model Artifacts

Folder ini menyimpan artifact model portable dan provenance model. TensorRT `.engine` tidak dicommit karena bersifat target-specific.

Setelah clone:

```bash
git lfs install
git lfs pull
python3 scripts/model_preflight.py
```

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

Jangan membuat engine di macOS, Windows, x86, atau GPU lain lalu menganggapnya valid untuk Manifold. Gunakan Manifold 3 atau environment Linux aarch64 dengan CUDA 11.4 dan TensorRT 8.5.2.2.
