# Build Dan Deployment

Dokumen ini menjelaskan flow build/deployment berdasarkan source dan evidence. Tidak ada build, DPK, deploy, SSH, atau inference yang dijalankan saat finalisasi handover.

## Status Saat Ini

| Item | Status |
|---|---|
| Latest built DPK | VERIFIED: v26 / `00.01.00.26` |
| Latest deployed runtime | VERIFIED: v26 |
| v27 source | VERIFIED: canonical source di repository ini |
| v27 build | BELUM |
| v27 package/DPK | BELUM |
| v27 deploy | BELUM |
| v27 runtime hardware validation | BELUM |

## Target Environment

Environment berikut terverifikasi dari snapshot Manifold:

| Komponen | Versi |
|---|---|
| OS | Ubuntu 20.04.6 LTS |
| Architecture | aarch64 |
| Device | DJI Manifold 3 / NVIDIA Orin NX |
| Python | 3.8.10 |
| GCC/G++ | 9.4.0 |
| CMake | 3.16.3 |
| CUDA | 11.4 |
| TensorRT | 8.5.2.2 |
| OpenCV | 4.5.4 |
| PSDK | 3.16.0 |

## Flow Build/Deployment

```text
source
  -> CMake
  -> native binary
  -> Python payload
  -> staging
  -> app.json
  -> DJI/PSDK DPK builder
  -> .dpk
  -> dji_app_ctl
  -> /open_app/ggp-drone-ai
```

Path `/open_app/ggp-drone-ai` adalah path runtime pada Manifold.

## Source

Canonical source:

```text
CMakeLists.txt
src/psdk/main.cpp
src/gap_plot_ai/
config/app.yaml
config/live.yaml
config/widget/
scripts/
dpk/
```

Credential DJI lokal diperlukan saat build native:

```text
config/dji_sdk_app_info.local.h
```

File tersebut tidak boleh dicommit. Template berada di `config/dji_sdk_app_info.example.h`.

## Native Build

Status untuk v27: INFERRED, belum dijalankan pada task ini.

Status historis v26: VERIFIED melalui DPK/installed runtime evidence.

`CMakeLists.txt` membatasi build native ke Linux aarch64 dan membutuhkan:

```text
PSDK_ROOT=/path/to/official/Payload-SDK-3.16.0
```

CMake memverifikasi hash library PSDK aarch64:

```text
c940d6d88f449ef6f48e535e0b765f4bf8486db44702d4d64e37da156d204426
```

Native app dibangun dari `src/psdk/main.cpp` dan link ke upstream PSDK library. OSAL/HAL tetap berasal dari `PSDK_ROOT`, bukan dari repository ini.

## Python Payload

Status v27: INFERRED.

Runtime installed v26 memakai payload Python dan config pada struktur installed app Manifold:

```text
/open_app/ggp-drone-ai/payload/python/bin/python3
/open_app/ggp-drone-ai/payload/python/lib/python3.8/site-packages/gap_plot_ai
/open_app/ggp-drone-ai/payload/share/config/live.yaml
```

Source v27 native memiliki `SpawnPythonWorker()` yang menjalankan:

```text
python3 -m gap_plot_ai.worker --config <payload>/share/config/live.yaml --ipc-dir <ipc> --backend engine
```

## DPK Packaging

Status v27: BELUM.

File yang relevan:

```text
scripts/build_dpk.sh
scripts/dpk_launcher.sh
scripts/live_config_env.py
scripts/validate_dpk.py
dpk/app.json.in
config/live.yaml
config/app.yaml
config/widget/
```

Staging yang diharapkan:

```text
dpk/staging/bin/gap_plot_ai_launcher
dpk/staging/payload/bin/gap_plot_ai_native
dpk/staging/payload/bin/live_config_env.py
dpk/staging/payload/share/config/app.yaml
dpk/staging/payload/share/config/live.yaml
dpk/staging/payload/share/config/widget/
dpk/staging/app.json
```

DPK builder yang digunakan adalah tool resmi PSDK:

```text
${PSDK_ROOT}/tools/build_dpk/build_dpk.sh
```

TensorRT `.engine` tidak boleh dimasukkan ke DPK staging secara tidak sengaja. Engine runtime berada pada path protected di Manifold.

## Install Dan Runtime

Status: VERIFIED untuk v26, belum untuk v27.

Command operasional yang terdokumentasi pada Manifold:

```text
dji_app_ctl start ggp-drone-ai
dji_app_ctl stop ggp-drone-ai
```

Installed app terakhir:

```text
version: 00.01.00.26
runtime: /open_app/ggp-drone-ai
```

## DJI Version / Debug Drift

Audit version indikator:

| Area | Kategori | Catatan |
|---|---|---|
| v26 installed runtime `00.01.00.26` | CURRENT RUNTIME | Runtime terakhir yang terbukti terinstall |
| v27 source | CURRENT DEVELOPMENT | Source canonical terbaru, belum artifact |
| `src/psdk/main.cpp` `debugVersion` | STALE/UNKNOWN | Perlu direkonsiliasi sebelum build v27 |
| `scripts/build_dpk.sh` hardcoded output version | STALE/UNKNOWN | Jangan dipakai untuk klaim v27 tanpa review |
| `dpk/app.json.in` | STALE/UNKNOWN | Perlu diselaraskan dengan app identity/version v27 |
| `staging-v26/app.json` historis | HISTORICAL | Bukti v26 staging, bukan template final v27 |
| launcher vs native worker spawn | OPEN | Pilih satu path sebelum packaging v27 |

Keputusan aman: dokumentasikan drift ini dan jangan mengklaim build configuration v27 sudah validated.

## Gate Sebelum Build v27

Sebelum build/package:

1. pilih version v27 yang benar;
2. reconcile native version metadata;
3. reconcile `dpk/app.json.in`;
4. putuskan startup path: launcher atau native spawn;
5. pastikan config engine path sesuai policy;
6. jalankan build hanya di Manifold 3 atau environment identik.

## Unknown

- versi DPK final untuk v27;
- apakah existing test suite perlu update setelah source v27 masuk;
- apakah v27 compile bersih pada target;
- apakah v27 runtime stabil pada hardware;
- apakah Pilot overlay v27 sudah sesuai di lapangan.
