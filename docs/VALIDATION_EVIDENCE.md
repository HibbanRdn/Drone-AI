# Validation Evidence

Dokumen ini mengkurasi evidence yang paling penting untuk handover. Evidence lengkap/raw tidak dimasukkan seluruhnya agar repository tetap fokus dan tidak menjadi dump snapshot.

## Lokasi Evidence

```text
docs/evidence/
  v26/
  v27/
  runtime/
```

Aturan interpretasi:

```text
source PASS tidak sama dengan DPK built
fixture PASS tidak sama dengan runtime deployed
runtime proof v26 tidak sama dengan runtime proof v27
```

## V26 Blocker

Tujuan:

Membuktikan bug `V26-SOFTWARE-BLOCKER-001` pada coordinate back-projection gap.

Input:

Fixture gap-positive v26 dengan expected gap pada koordinat lokal sekitar `(520, 100)`.

Hasil:

v26 menemukan count/slot gap yang benar, tetapi posisi gap bergeser:

```text
expected:  (520, 100)
v26:       (522.89, 267.01)
corrected: (519.97, 99.66)
bug error: 167.04 px
```

Status:

```text
BLOCKER_CONFIRMED
```

Evidence:

```text
docs/evidence/v26/V26_SOFTWARE_BLOCKER_gap_coords.json
```

Makna:

v26 tidak aman dijadikan source final untuk output gap coordinate, walaupun runtime v26 adalah runtime terakhir yang terinstall.

Keterbatasan:

Evidence ini membuktikan blocker, bukan fix.

## V26 Forensic Runtime/Artifact

Tujuan:

Menjelaskan provenance v26 sebagai DPK/runtime terakhir.

Input:

Forensic validation final v26 dan continuation journal.

Hasil:

v26 tercatat sebagai built DPK `00.01.00.26` dan runtime terakhir yang terinstall. Installed native binary cocok dengan staging payload v26.

Status:

```text
VERIFIED_FOR_V26_PROVENANCE
```

Evidence:

```text
docs/evidence/v26/V26_FORENSIC_VALIDATION_FINAL.md
docs/evidence/v26/V26_FLASH_CONTINUATION_JOURNAL.md
```

Makna:

Jika ingin memahami runtime terakhir di Manifold, rujuk v26.

Keterbatasan:

v26 tetap memiliki Blocker-001 pada output gap coordinate.

## V27 Patch Provenance

Tujuan:

Mencatat patch source v27 untuk Blocker-001.

Input:

Diff v26 -> v27 pada `src/gap_plot_ai/finalizer.py`.

Hasil:

v27 menambahkan `_row_coords_to_local()` dan mengganti formula buggy `(rc - cy)` dengan pemakaian `rc` sebagai across coordinate.

Status:

```text
PATCH_RECORDED
PY_COMPILE_OK
```

Evidence:

```text
docs/evidence/v27/V27_BLOCKER001_PATCH.json
docs/evidence/v27/V27_PROVENANCE.json
```

Makna:

Source v27 sudah memuat fix yang benar secara provenance.

Keterbatasan:

Patch source tidak membuktikan v27 sudah dibuild atau deployed.

## V27 T01 Coordinate Inverse

Tujuan:

Memvalidasi inverse transform row-coordinate ke local-coordinate.

Input:

Orientasi 0, 30, 60, 90, 135, dan 179 derajat, plus reproduksi skenario blocker.

Hasil:

Roundtrip error berada jauh di bawah toleransi; buggy formula mereproduksi error besar.

Status:

```text
PASS
```

Evidence:

```text
docs/evidence/v27/V27_T01_COORD_INVERSE.json
```

Makna:

Fix matematis v27 valid untuk coordinate inverse.

Keterbatasan:

Pure math test; bukan runtime hardware v27.

## V27 T02 Gap Semantics

Tujuan:

Memastikan fix coordinate tidak merusak semantics gap analysis.

Input:

Fixture untuk zero gaps, multi-orientation gap, insufficient plants, dan low-coverage row exclusion.

Hasil:

Semua case PASS, termasuk gap position error di bawah toleransi.

Status:

```text
PASS
```

Evidence:

```text
docs/evidence/v27/V27_T02_GAP_SEMANTICS.json
```

Makna:

Fix v27 mempertahankan behavior gap semantics yang diharapkan.

Keterbatasan:

Fixture-level validation; bukan deployment proof.

## V27 T03 Positive Gap E2E

Tujuan:

Memvalidasi fixture positive gap end-to-end dengan finalizer.

Input:

Session fixture `SESSION_GAPPOS`.

Hasil:

```text
registration accepted: 2
registration rejected: 0
plants expected/found: 49 / 49
gap expected/found: 1 / 1
gap position error: 0.3445 px
row-end false gap: false
low-coverage false gap: false
```

Status:

```text
PASS
```

Evidence:

```text
docs/evidence/v27/V27_GAP_POSITIVE_EVIDENCE.json
```

Makna:

Fixture finalizer v27 menghasilkan posisi gap yang benar setelah fix.

Keterbatasan:

Ini bukan bukti bahwa v27 sudah menjadi DPK atau runtime hardware.

## Runtime Liveview Evidence

Tujuan:

Membuktikan runtime v26 terakhir pernah mencapai PSDK/liveview active state di Manifold.

Input:

Blackbox log terakhir yang dikurasi.

Hasil:

Log menunjukkan:

```text
PSDK version terdeteksi
device manifold3 teridentifikasi
Matrice 4E pada Extension Port terdeteksi
widget registration success
Decoded M4E RGB liveview subscription active
```

Status:

```text
VERIFIED_FOR_V26_RUNTIME_STARTUP
```

Evidence:

```text
docs/evidence/runtime/ggp-drone-ai_20260812_16-03-01.blackbox.txt
```

Makna:

PSDK/liveview path pernah aktif pada runtime v26.

Keterbatasan:

Run pendek, bukan long-duration validation, dan bukan runtime v27.

## Evidence Yang Tidak Dimasukkan

Repository ini sengaja tidak memasukkan:

- DPK binary;
- installed runtime mirror;
- full runtime sessions;
- TensorRT engine binary;
- full forensic source snapshot;
- evidence lama yang luas tetapi tidak semuanya kuat.

Kesimpulan penting sudah dikurasi di dokumen ini dan file kecil di `docs/evidence/`.
