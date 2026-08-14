# Known Issues And Blockers

Dokumen ini hanya mencatat issue yang didukung oleh source, evidence, atau provenance. Jangan mengubah status issue menjadi resolved hanya agar handover terlihat selesai.

## RESOLVED

### Blocker-001: gap coordinate back-projection

Status:

```text
RESOLVED_IN_V27_SOURCE
```

Evidence:

```text
docs/evidence/v26/V26_SOFTWARE_BLOCKER_gap_coords.json
docs/evidence/v27/V27_BLOCKER001_PATCH.json
docs/evidence/v27/V27_T01_COORD_INVERSE.json
docs/evidence/v27/V27_T02_GAP_SEMANTICS.json
docs/evidence/v27/V27_GAP_POSITIVE_EVIDENCE.json
```

Ringkasan:

v26 memakai `(rc - cy)` pada inverse row-coordinate. v27 memperbaikinya dengan `_row_coords_to_local()` dan memakai `rc` langsung sebagai across coordinate.

Catatan:

Fix ini resolved pada source v27, tetapi v27 belum build/package/deploy.

### Provenance v26 runtime terakhir

Status:

```text
RESOLVED_FOR_PROVENANCE
```

v26 terbukti sebagai latest built DPK dan latest deployed runtime pada snapshot. Ini bukan berarti v26 bebas bug, karena Blocker-001 ditemukan setelahnya.

## PARTIALLY RESOLVED

### Geospatial / local pixel limitation

Status:

```text
PARTIALLY_RESOLVED
```

`src/gap_plot_ai/geospatial_v2.py` memiliki utilitas WGS84/UTM dan transform. Namun output finalizer yang teramati masih menunjukkan local-pixel mapping pada sebagian result. SHP/GeoJSON tidak boleh diasumsikan sebagai true geographic output sampai divalidasi end-to-end dengan RTK/field evidence.

### Quality/provenance evidence lama

Status:

```text
PARTIALLY_RESOLVED
```

Snapshot raw memiliki banyak evidence historis. Sebagian evidence lama diketahui kurang kuat atau tidak konsisten. Repository ini memakai curated evidence yang paling relevan dan hash langsung, bukan semua manifest lama.

### Reconciliation GitHub-to-Manifold

Status:

```text
PARTIALLY_RESOLVED
```

Source v27 Manifold sudah dikonsolidasikan ke repository. Namun test suite, build, package, dan runtime validation v27 belum dijalankan.

## OPEN

### v27 belum build

Status:

```text
OPEN
```

Source v27 sudah canonical, tetapi belum dibuild pada Manifold 3 atau target identik.

### v27 belum package menjadi DPK

Status:

```text
OPEN
```

Belum ada DPK v27. DPK terakhir tetap v26 / `00.01.00.26`.

### v27 belum deploy

Status:

```text
OPEN
```

Runtime terakhir pada Manifold tetap v26.

### RegistrationFrameWriter

Status:

```text
OPEN
```

Pada `src/gap_plot_ai/worker.py`, `RegistrationFrameWriter` dibuat ketika session start tetapi kemudian `frame_writer = None` langsung dieksekusi. Perilaku ini perlu direview karena dapat memengaruhi registration frame dan finalizer quality.

### Launcher/native-worker startup reconciliation

Status:

```text
OPEN
```

Source memiliki `scripts/dpk_launcher.sh` dan native `SpawnPythonWorker()`. v26 staging historis memakai direct native binary. Developer berikutnya perlu memilih satu startup path sebelum packaging v27.

### Field validation

Status:

```text
OPEN
```

Fixture validation PASS, tetapi field validation untuk plantation aktual belum lengkap.

### RTK/geospatial validation

Status:

```text
OPEN
```

Telemetry/RTK field ada dalam source, tetapi true geospatial output belum validated end-to-end.

### Pilot overlay long-run validation

Status:

```text
OPEN
```

Runtime v26 pernah mencapai liveview active state, tetapi long-run overlay alignment, stale metadata clearing, dan box count behavior masih perlu diuji.

### Gap precision/recall

Status:

```text
OPEN
```

Perlu field truth untuk plant detection precision/recall, gap count, gap coordinate, false positive row-end/headland/road, dan kondisi crop/lights/altitude.

## BLOCKED / ENVIRONMENT DEPENDENT

### Hardware DJI

Status:

```text
ENVIRONMENT_DEPENDENT
```

Build/deploy/runtime validation membutuhkan Manifold 3, aircraft Matrice 4E, DJI Pilot, dan workflow PSDK/DJI yang sesuai.

### Windows/DJI tooling

Status:

```text
ENVIRONMENT_DEPENDENT
```

Sebagian workflow DJI dapat bergantung pada tooling/operator environment Windows. Repository mendokumentasikan flow, tetapi tidak menggantikan kebutuhan device/tooling.

### TensorRT engine validation

Status:

```text
ENVIRONMENT_DEPENDENT
```

TensorRT engine harus dibuat atau divalidasi pada Manifold 3 atau environment identik. Jangan memakai engine dari macOS/Windows/x86/unrelated GPU sebagai valid runtime artifact.

## UNKNOWN

### Versi final v27

Status:

```text
UNKNOWN
```

Belum diputuskan app/DPK version final untuk v27.

### Full test suite setelah konsolidasi

Status:

```text
UNKNOWN
```

Tidak ada test suite yang dijalankan saat finalisasi handover karena scope melarang build/inference/development lanjut. Test strategy perlu direview developer berikutnya.

### Compile status v27

Status:

```text
UNKNOWN
```

Belum ada build v27 pada target environment.
