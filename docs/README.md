# Dokumentasi Handover

Gunakan dokumen di bawah ini sebagai sumber utama setelah membaca `README.md` dan `HANDOVER.md`.

## Dokumen Canonical

| Dokumen | Kegunaan |
|---|---|
| `INITIAL_SETUP.md` | Inventory hardware, firmware, wiring, firmware update, konektivitas, PSDK, dan checklist setup awal |
| `SOURCE_PROVENANCE.md` | Menjelaskan hubungan Git baseline, Manifold v26/v27, DPK v26, runtime v26, dan source canonical |
| `ARCHITECTURE.md` | Menjelaskan flow Matrice 4E -> PSDK -> native C++ -> IPC -> Python worker -> TensorRT -> finalizer |
| `CODE_WALKTHROUGH.md` | Menelusuri entry point, API PSDK, IPC, worker, runtime, finalizer, dan lokasi perubahan source |
| `AI_PIPELINE.md` | Menjelaskan detector, segmenter, TensorRT engine, preprocessing, NMS, finalizer, dan gap analysis |
| `MODEL_DEPLOYMENT.md` | Prosedur artifact `.pt` -> ONNX -> TensorRT, validasi, aktivasi, dan rollback model |
| `BUILD_DEPLOYMENT.md` | Menjelaskan build/deployment flow dan status VERIFIED/INFERRED/UNKNOWN |
| `VALIDATION_EVIDENCE.md` | Mengkurasi evidence v26 blocker, v27 T01/T02/T03, dan runtime liveview evidence |
| `KNOWN_ISSUES_AND_BLOCKERS.md` | Mengelompokkan issue RESOLVED, PARTIALLY RESOLVED, OPEN, BLOCKED, UNKNOWN |
| `TROUBLESHOOTING_MANIFOLD.md` | Troubleshooting praktis untuk app, PSDK, liveview, worker, TensorRT, overlay, finalizer, DPK |
| `NEXT_DEVELOPER_ROADMAP.md` | Urutan kerja developer berikutnya dari preserve state sampai field validation |

## Urutan Baca

1. `README.md` dan `HANDOVER.md` di root repository.
2. `INITIAL_SETUP.md` dan `SOURCE_PROVENANCE.md`.
3. `ARCHITECTURE.md` dan `CODE_WALKTHROUGH.md`.
4. `AI_PIPELINE.md` dan `MODEL_DEPLOYMENT.md`.
5. `BUILD_DEPLOYMENT.md`, validation, issue, troubleshooting, dan roadmap.

## Dokumen Historis

File dokumentasi lain di folder ini berasal dari fase development sebelum konsolidasi v27. Dokumen tersebut tetap dipertahankan sebagai konteks historis, tetapi tidak boleh mengalahkan status canonical:

```text
latest canonical development = v27
latest built DPK = v26 / 00.01.00.26
latest deployed runtime = v26
v27 belum build/package/deploy
```

Jika ada perbedaan antara dokumen historis dan dokumen canonical, gunakan dokumen canonical.
