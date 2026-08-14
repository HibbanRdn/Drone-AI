# Dokumentasi Handover

Gunakan dokumen di bawah ini sebagai sumber utama setelah membaca `README.md` dan `HANDOVER.md`.

## Dokumen Canonical

| Dokumen | Kegunaan |
|---|---|
| `SOURCE_PROVENANCE.md` | Menjelaskan hubungan Git baseline, Manifold v26/v27, DPK v26, runtime v26, dan source canonical |
| `ARCHITECTURE.md` | Menjelaskan flow Matrice 4E -> PSDK -> native C++ -> IPC -> Python worker -> TensorRT -> finalizer |
| `AI_PIPELINE.md` | Menjelaskan detector, segmenter, TensorRT engine, preprocessing, NMS, finalizer, dan gap analysis |
| `BUILD_DEPLOYMENT.md` | Menjelaskan build/deployment flow dan status VERIFIED/INFERRED/UNKNOWN |
| `VALIDATION_EVIDENCE.md` | Mengkurasi evidence v26 blocker, v27 T01/T02/T03, dan runtime liveview evidence |
| `KNOWN_ISSUES_AND_BLOCKERS.md` | Mengelompokkan issue RESOLVED, PARTIALLY RESOLVED, OPEN, BLOCKED, UNKNOWN |
| `TROUBLESHOOTING_MANIFOLD.md` | Troubleshooting praktis untuk app, PSDK, liveview, worker, TensorRT, overlay, finalizer, DPK |
| `NEXT_DEVELOPER_ROADMAP.md` | Urutan kerja developer berikutnya dari preserve state sampai field validation |

## Dokumen Historis

File dokumentasi lain di folder ini berasal dari fase development sebelum konsolidasi v27. Dokumen tersebut tetap dipertahankan sebagai konteks historis, tetapi tidak boleh mengalahkan status canonical:

```text
latest canonical development = v27
latest built DPK = v26 / 00.01.00.26
latest deployed runtime = v26
v27 belum build/package/deploy
```

Jika ada perbedaan antara dokumen historis dan dokumen canonical, gunakan dokumen canonical.
