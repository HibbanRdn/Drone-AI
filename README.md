# Drone AI Gap Plot

**MULAI DI SINI.**

Repository ini adalah entry point handover untuk project Drone AI Gap Plot pada DJI Matrice 4E + DJI Manifold 3. Tujuan aplikasi adalah menjalankan AI onboard di Manifold melalui DJI Payload SDK, membaca liveview kamera M4E, menjalankan inference TensorRT, menampilkan bounding box ke DJI Pilot, lalu menyimpan output session untuk analisis tanaman dan gap plot.

## Project

Project ini menargetkan workflow drone untuk inspeksi plantation pineapple:

```text
DJI Matrice 4E
  + DJI Manifold 3
  + DJI Payload SDK / PSDK
  + TensorRT inference
  + detector + segmenter
  + finalizer / gap analysis
```

Repository ini sudah dikonsolidasikan agar developer berikutnya tidak perlu membaca snapshot eksternal atau chat lama untuk memahami kondisi project.

## Arsitektur Singkat

```text
DJI Matrice 4E
  -> PSDK Liveview
  -> native C++ app
  -> frame IPC
  -> Python worker
  -> TensorRT detector + segmenter
  -> finalizer / gap analysis
  -> Pilot overlay + session output
```

Source utama ada di:

```text
src/gap_plot_ai/
src/psdk/
config/
```

## Status Saat Ini

| Item | Status |
|---|---|
| Latest Git baseline sebelum konsolidasi | `3d92c342c36ae5912c9d71047fab6e43c936f080` |
| Latest canonical development | v27 |
| Latest built DPK | v26 / `00.01.00.26` |
| Latest deployed runtime | v26 |
| v27 build | BELUM |
| v27 package/DPK | BELUM |
| v27 deploy ke Manifold | BELUM |
| v27 runtime hardware validation | BELUM |

Poin paling penting:

```text
latest source development = v27
latest runtime deployed   = v26
```

v27 berisi fix untuk `V26-SOFTWARE-BLOCKER-001` pada `src/gap_plot_ai/finalizer.py`. Evidence source/fixture menunjukkan PASS, tetapi v27 belum pernah menjadi runtime terinstall di Manifold.

## Model

Artifact training dan ONNX tersedia di `models/`. TensorRT `.engine` bersifat target-specific dan tidak disimpan sebagai binary di repository ini.

Runtime Manifold yang terdokumentasi memakai engine eksternal pada device:

```text
/home/dji/gap_plot_ai_assets/models/engine/plant_center_manual_v1_b0_best_fp16.engine
/home/dji/gap_plot_ai_assets/models/engine/plot_segmenter_b4_selected_best_fp16.engine
```

Detail model ada di [docs/AI_PIPELINE.md](docs/AI_PIPELINE.md) dan [models/README.md](models/README.md).

## Baca Selanjutnya

Urutan baca untuk developer berikutnya:

1. [HANDOVER.md](HANDOVER.md)
2. [docs/README.md](docs/README.md)
3. [docs/SOURCE_PROVENANCE.md](docs/SOURCE_PROVENANCE.md)
4. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
5. [docs/AI_PIPELINE.md](docs/AI_PIPELINE.md)
6. [docs/BUILD_DEPLOYMENT.md](docs/BUILD_DEPLOYMENT.md)
7. [docs/VALIDATION_EVIDENCE.md](docs/VALIDATION_EVIDENCE.md)
8. [docs/KNOWN_ISSUES_AND_BLOCKERS.md](docs/KNOWN_ISSUES_AND_BLOCKERS.md)
9. [docs/TROUBLESHOOTING_MANIFOLD.md](docs/TROUBLESHOOTING_MANIFOLD.md)
10. [docs/NEXT_DEVELOPER_ROADMAP.md](docs/NEXT_DEVELOPER_ROADMAP.md)

## Safety

Jangan commit credential. Credential DJI lokal berada di:

```text
config/dji_sdk_app_info.local.h
```

File tersebut di-ignore oleh Git. Gunakan `config/dji_sdk_app_info.example.h` sebagai template.
