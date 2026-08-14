# Ringkasan Forensic Validation v26

Dokumen ini adalah ringkasan kurasi dari forensic validation v26. File ini dipertahankan untuk provenance, bukan untuk menyatakan v26 bebas issue.

## Status

```text
Latest built DPK: v26 / 00.01.00.26
Latest deployed runtime: v26
Known blocker: V26-SOFTWARE-BLOCKER-001
```

## Fakta Penting

- DPK v26 tercatat sebagai artifact terakhir yang berhasil dibuat.
- Staging payload v26 dan installed runtime v26 cocok pada hash native binary.
- Runtime terakhir pada Manifold adalah v26.
- v27 belum dibuat menjadi DPK dan belum dideploy.

## Makna Untuk Handover

v26 adalah bukti runtime/deployment terakhir, tetapi bukan source yang harus dilanjutkan. Development berikutnya harus dimulai dari source v27 di repository ini.

## Keterbatasan

v26 memiliki Blocker-001 pada coordinate back-projection gap. Gunakan file `V26_SOFTWARE_BLOCKER_gap_coords.json` untuk detail bug.
