# Handover Drone AI Gap Plot

Dokumen ini adalah ringkasan teknis saat project Drone AI Gap Plot diserahkan. Setelah membaca `README.md` dan dokumen ini, developer berikutnya harus memahami posisi source, runtime terakhir, blocker, evidence, dan langkah lanjutan tanpa membutuhkan snapshot eksternal.

## Ringkasan Project

Drone AI Gap Plot adalah aplikasi onboard untuk DJI Matrice 4E + DJI Manifold 3. Aplikasi native C++ memakai DJI Payload SDK untuk mengambil decoded RGB liveview dari kamera M4E, mengirim frame terbaru ke Python worker melalui IPC, menjalankan inference TensorRT, mengirim bounding box terpilih ke DJI Pilot, dan menutup session dengan finalizer untuk plant/gap analysis.

## Kondisi Terakhir

| Item | Status |
|---|---|
| Development terbaru | v27 |
| Build terakhir | v26 |
| DPK terakhir | `00.01.00.26` |
| Runtime terakhir di Manifold | v26 |
| Git baseline sebelum konsolidasi | `3d92c342c36ae5912c9d71047fab6e43c936f080` |
| Branch canonical | `feature/pilot-liveview-inference` |

Status v27:

```text
Source development terbaru: YA
Validation source fix: PASS
Sudah di-build: BELUM
Sudah menjadi DPK: BELUM
Sudah di-deploy ke Manifold: BELUM
Sudah diuji sebagai runtime v27 pada hardware: BELUM
```

## Apa yang Sudah Berhasil

Berbasis evidence snapshot yang sudah dikurasi:

- v26 pernah berhasil dibuat menjadi DPK `00.01.00.26`.
- v26 adalah runtime terakhir yang terinstall pada Manifold.
- Binary native installed v26 cocok dengan staging payload v26.
- PSDK 3.16 berhasil inisialisasi pada Manifold 3 dan mendeteksi Matrice 4E pada Extension Port.
- Decoded RGB liveview M4E mencapai status aktif pada blackbox evidence terakhir.
- v27 source memuat fix untuk `V26-SOFTWARE-BLOCKER-001`.
- Evidence v27 T01/T02/T03 menunjukkan PASS untuk coordinate inverse, gap semantics, dan positive gap E2E fixture.

## Perubahan v27

Perubahan penting v27 ada pada:

```text
src/gap_plot_ai/finalizer.py
```

v26 memiliki bug pada back-projection koordinat gap. Kode v26 memakai `(rc - cy)` padahal `rc` sudah berada pada satuan across-axis. Efeknya, gap count dan slot dapat benar, tetapi posisi gap bergeser pada output lokal/GeoJSON/SHP.

v27 menambahkan helper:

```text
_row_coords_to_local(along, across, cx, cy, theta)
```

Rumus fix:

```text
local_x = cx + along * cos(theta) - across * sin(theta)
local_y = cy + along * sin(theta) + across * cos(theta)
```

Evidence utama:

- `docs/evidence/v26/V26_SOFTWARE_BLOCKER_gap_coords.json`
- `docs/evidence/v27/V27_BLOCKER001_PATCH.json`
- `docs/evidence/v27/V27_T01_COORD_INVERSE.json`
- `docs/evidence/v27/V27_T02_GAP_SEMANTICS.json`
- `docs/evidence/v27/V27_GAP_POSITIVE_EVIDENCE.json`

## Yang Belum Dilakukan

Bagian ini tidak boleh ditafsirkan longgar:

```text
v27 belum build
v27 belum package menjadi DPK
v27 belum deploy
v27 belum runtime validation pada hardware
```

Runtime yang terakhir benar-benar terinstall masih v26.

## Known Issues Ringkas

- Version/build metadata perlu direkonsiliasi sebelum v27 packaging.
- Startup path worker perlu dipastikan satu arah: launcher atau native spawn.
- `RegistrationFrameWriter` pada v27 perlu review karena writer dibuat lalu di-reset ke `None`.
- Geospatial output masih perlu validasi end-to-end; beberapa output snapshot masih bersifat local-pixel.
- Field validation, RTK/geospatial validation, Pilot overlay long-run validation, dan gap precision/recall masih open.

Detail ada di [docs/KNOWN_ISSUES_AND_BLOCKERS.md](docs/KNOWN_ISSUES_AND_BLOCKERS.md).

## Source Canonical

Untuk development Manifold berikutnya, mulai dari source repository ini:

```text
src/gap_plot_ai/
src/psdk/
config/
scripts/
dpk/
CMakeLists.txt
```

Jangan mulai dari installed runtime mirror, staging lama, folder forensic, atau copy `vnext-v26`/`vnext-v27` mentah.

## Developer Berikutnya Mulai Dari Mana

1. Baca [docs/SOURCE_PROVENANCE.md](docs/SOURCE_PROVENANCE.md).
2. Baca [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
3. Baca [docs/AI_PIPELINE.md](docs/AI_PIPELINE.md).
4. Review source canonical di `src/` dan config di `config/`.
5. Reconcile metadata versi/build seperti dijelaskan di [docs/BUILD_DEPLOYMENT.md](docs/BUILD_DEPLOYMENT.md).
6. Review issue di [docs/KNOWN_ISSUES_AND_BLOCKERS.md](docs/KNOWN_ISSUES_AND_BLOCKERS.md).
7. Ikuti roadmap di [docs/NEXT_DEVELOPER_ROADMAP.md](docs/NEXT_DEVELOPER_ROADMAP.md).

Prioritas berikutnya adalah correctness dan provenance build, bukan feature baru atau optimasi.
