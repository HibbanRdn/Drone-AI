# Build dan Deployment

Dokumen ini menjelaskan prosedur yang direkonstruksi dari source canonical, script, dan evidence Manifold. Seluruh command di bawah adalah panduan; finalisasi handover tidak menjalankan build, membuat DPK, melakukan deploy, atau menguji hardware.

Label status: **VERIFIED** didukung source/artifact/evidence; **INFERRED** tersedia di source atau script tetapi belum dibuktikan untuk v27; **UNKNOWN** belum memiliki bukti cukup.

## Status Versi

| Item | Status |
|---|---|
| Source canonical | **VERIFIED**: v27 di repository ini |
| Build terakhir | **VERIFIED**: v26 |
| DPK terakhir | **VERIFIED**: `00.01.00.26` |
| Runtime terakhir | **VERIFIED**: v26 di `/open_app/ggp-drone-ai` pada Manifold |
| Build/package/deploy v27 | **BELUM** |
| Runtime validation v27 pada hardware | **BELUM** |

Hasil T01/T02/T03 hanya memvalidasi source fix finalizer. Hasil tersebut bukan bukti bahwa v27 dapat di-build atau berjalan pada drone.

## Environment Target

Environment berikut **VERIFIED** dari snapshot Manifold terakhir, bukan persyaratan untuk membekukan seluruh deployment berikutnya pada versi tersebut.

| Komponen | Versi yang diamati |
|---|---|
| Hardware | DJI Manifold 3 / NVIDIA Orin NX |
| OS | Ubuntu 20.04.6 LTS |
| Architecture | aarch64 |
| PSDK | 3.16.0 |
| CUDA | 11.4 |
| TensorRT | 8.5.2.2 |
| OpenCV | 4.5.4 |
| Python | 3.8.10 |
| GCC/G++ | 9.4.0 |
| CMake | 3.16.3 |

Sebelum menyiapkan perangkat baru, baca [Initial Setup](INITIAL_SETUP.md). Firmware terbaru perlu diperiksa kembali pada situs resmi DJI; kompatibilitas engine TensorRT harus divalidasi terhadap environment target aktual.

## Gambaran Alur

```text
repository canonical
  -> paket transfer offline terverifikasi
  -> install source + PSDK pada Manifold
  -> CMake native build
  -> ground/runtime gates
  -> Python runtime bundle
  -> DPK staging + app.json
  -> DJI/PSDK build_dpk
  -> .dpk + SHA-256
  -> install melalui DJI Pilot 2 atau dji_app_ctl
  -> /open_app/ggp-drone-ai
```

Ada dua jalur yang tidak boleh dicampur:

1. **Development/debug**: kirim paket offline terverifikasi, build di Manifold, lalu jalankan ground test terkontrol.
2. **Formal deployment**: setelah seluruh gate lulus, buat DPK resmi dan install melalui mekanisme aplikasi DJI.

`scripts/deploy_dev.sh` sengaja menolak direct working-tree deployment. Ini mencegah source laptop disalin langsung menjadi runtime tanpa provenance.

## Prasyarat Repository

Source canonical:

```text
CMakeLists.txt
src/psdk/main.cpp
src/gap_plot_ai/
config/app.yaml
config/live.yaml
config/widget/
scripts/
dpk/
deployment/offline/
```

Credential DJI harus disediakan secara lokal pada `config/dji_sdk_app_info.local.h`. Mulai dari `config/dji_sdk_app_info.example.h`. File lokal tersebut di-ignore dan tidak boleh masuk Git, paket publik, log, atau dokumentasi.

PSDK yang digunakan source ini adalah upstream resmi 3.16.0. `CMakeLists.txt` dan `scripts/verify_psdk_316.py` memverifikasi platform serta artifact PSDK; repository tidak menyimpan vendor tree PSDK.

## Jalur A: Paket Offline untuk Development

Status untuk v27: **INFERRED**, karena script tersedia tetapi build v27 belum dijalankan. Jalur ini direkomendasikan untuk memindahkan source dan dependency secara terkendali dari Windows ke Manifold.

### 1. Siapkan paket pada Windows

Entry point:

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\deploy_manifold_windows.ps1 `
  -Repository C:\path\to\Drone-AI `
  -PsdkRoot C:\path\to\Payload-SDK-3.16.0 `
  -OfflineWheels C:\path\to\offline_wheels `
  -CredentialHeader C:\path\to\dji_sdk_app_info.local.h `
  -SshHost gap-plot-manifold
```

Script memerlukan Windows, branch `feature/pilot-liveview-inference`, working tree bersih, PSDK 3.16.0, credential header lokal, offline wheels, dan SSH/SCP ke Manifold. Script menjalankan test packaging, membentuk archive, membuat sidecar SHA-256, memverifikasi ulang archive, lalu hanya mentransfer package ke `/home/dji/gap_plot_ai_transfer`. Transfer bukan install, build, atau deploy.

### 2. Verifikasi dan install source offline

Pada Manifold, ikuti [Manifold First Run](MANIFOLD_FIRST_RUN.md). Inti prosedurnya:

```bash
cd /home/dji/gap_plot_ai_transfer
PACKAGE="/home/dji/gap_plot_ai_transfer/<exact-package-name>.tar.gz"
sha256sum -c "${PACKAGE}.sha256"
tar -tzf "${PACKAGE}"
tar -xzf "${PACKAGE}"
PACKAGE_ROOT="${PACKAGE%.tar.gz}"
python3 "${PACKAGE_ROOT}/install/verify_offline_archive.py" \
  "${PACKAGE}" --sidecar "${PACKAGE}.sha256"
cd "${PACKAGE_ROOT}"
timeout 60s ./install/verify_offline.sh
timeout 120s ./install/install_offline.sh
```

Installer membuat release immutable di bawah `/home/dji/gap_plot_ai_dev/releases/<commit>/` dan PSDK terverifikasi di vendor root. Path tersebut adalah path target Manifold, bukan kebutuhan filesystem laptop developer.

### 3. Inventory dan build offline

```bash
timeout 60s bash "${APP_SOURCE}/scripts/manifold_inventory_readonly.sh" \
  | tee "${APP_SOURCE}/runtime/reports/manifold_inventory.txt"
timeout 300s ./install/build_manifold_offline.sh
```

`deployment/offline/build_manifold_offline.sh` menolak platform selain Linux aarch64, memverifikasi source PSDK, membuat venv `--system-site-packages`, memasang wheel offline yang dideklarasikan, membangun sample resmi PSDK dan native app, lalu memeriksa `ldd`. Build ini tidak menguji TensorRT, liveview aircraft, metadata Pilot, atau finalization session.

## Native Build Langsung pada Manifold

Jika source dan PSDK sudah tersedia secara terverifikasi:

```bash
cd /path/to/Drone-AI
export PSDK_ROOT=/path/to/Payload-SDK-3.16.0
cp config/dji_sdk_app_info.example.h config/dji_sdk_app_info.local.h
# Isi file lokal dengan credential DJI yang sah, tanpa mencetak nilainya.
./scripts/build_manifold.sh
```

Command efektif:

```bash
cmake -S . -B build \
  -DPSDK_ROOT="${PSDK_ROOT}" \
  -DGAP_PLOT_AI_APP_INFO_HEADER="config/dji_sdk_app_info.local.h" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 2
```

Output yang diharapkan adalah `build/bin/gap_plot_ai`. `CMakeLists.txt` hanya menerima Linux aarch64 dan menghubungkan aplikasi ke `libpayloadsdk.a` resmi. OSAL/HAL berasal dari PSDK upstream.

## Ground Test Sebelum DPK

Lakukan pada aircraft yang aman di tanah dan DJI Pilot 2 aktif. Urutan minimum:

1. inventory hardware dan dependency;
2. verifikasi sample resmi PSDK untuk M4E visual stream dan widget;
3. verifikasi static five-box overlay di DJI Pilot;
4. validasi engine read-only, tanpa rebuild atau rename;
5. jalankan aplikasi development dengan timeout dan monitoring terpisah;
6. periksa liveview, worker heartbeat, result freshness, session output, dan cleanup;
7. buat marker gate hanya berdasarkan evidence nyata.

Command yang disiapkan repository dijelaskan pada [Manifold First Run](MANIFOLD_FIRST_RUN.md). Jangan membuat file gate hanya untuk melewati packaging.

## Jalur B: Formal DPK Packaging

Status v27: **BELUM DIJALANKAN**.

Periksa kebutuhan tanpa membangun:

```bash
./scripts/build_dpk.sh --check
```

Script menuntut:

```text
build/bin/gap_plot_ai
runtime/dpk_bundle/python/bin/python3
runtime/gates/psdk_liveview_verified
runtime/gates/engine_parity_verified
runtime/gates/ground_test_verified
runtime/gates/dpk_runtime_bundle_verified
```

Setelah seluruh gate sah dan metadata versi direkonsiliasi:

```bash
export PSDK_ROOT=/path/to/Payload-SDK-3.16.0
export MANIFOLD_VER_MIN=<verified-minimum-firmware>
export MANIFOLD_VER_MAX=<verified-maximum-firmware>
./scripts/build_dpk.sh
```

Script menolak platform selain Linux aarch64; memastikan tool `${PSDK_ROOT}/tools/build_dpk/build_dpk.sh`; membuat ulang staging; menyalin runtime Python, native binary, launcher, config, dan widget; menolak `*.engine`; mengisi batas firmware; memvalidasi staging; memanggil tool resmi DJI; lalu membuat SHA-256 DPK.

Struktur staging:

```text
dpk/staging/
  app.json
  bin/gap_plot_ai_launcher
  payload/bin/gap_plot_ai_native
  payload/bin/live_config_env.py
  payload/python/
  payload/share/config/app.yaml
  payload/share/config/live.yaml
  payload/share/config/widget/
```

Engine TensorRT tidak dibundel. Runtime mengharapkan engine terverifikasi di `/home/dji/gap_plot_ai_assets/models/engine`. Lihat [Model Deployment](MODEL_DEPLOYMENT.md) sebelum memilih artifact.

## Install dan Manajemen Aplikasi

### DJI Pilot 2

Metode yang direkomendasikan untuk menguji mekanisme production startup:

1. salin `.dpk` ke storage DJI RC Plus 2 Enterprise atau microSD;
2. buka DJI Pilot 2;
3. masuk ke halaman **Manifold 3**;
4. buka **Application Management**;
5. tekan ikon `+` dan pilih file DPK;
6. ikuti status install dan verifikasi versi aplikasi.

### `dji_app_ctl`

Tool development/debug pada Manifold:

```bash
dji_app_ctl install -i /path/to/ggp-drone-ai_v<version>.dpk
dji_app_ctl list
dji_app_ctl status ggp-drone-ai
dji_app_ctl start ggp-drone-ai
dji_app_ctl stop ggp-drone-ai
dji_app_ctl restart ggp-drone-ai
dji_app_ctl enable ggp-drone-ai
dji_app_ctl disable ggp-drone-ai
dji_app_ctl uninstall ggp-drone-ai
```

DJI menyatakan startup melalui development tool dan DJI Pilot/FlightHub dapat berbeda, termasuk high-performance mode. Kelulusan `dji_app_ctl start` saja tidak cukup untuk menutup validation deployment.

| Tool | Fungsi |
|---|---|
| PSDK `install.py` | Memasang dependency sistem atau `.deb` tanpa root pada area PSDK; bukan installer DPK aplikasi ini |
| `dji_app_ctl` | Mengelola install dan lifecycle aplikasi DPK |
| DJI Pilot 2 Application Management | Jalur operator untuk memasang DPK dan menguji lifecycle mendekati production |

## Runtime, Data, dan Log

Runtime v26 terakhir terverifikasi di `/open_app/ggp-drone-ai`. DPK launcher mengarahkan log aplikasi ke:

```text
/open_app/ggp-drone-ai/data/logs/native.log
/open_app/ggp-drone-ai/data/logs/worker.log
```

Data session dan runtime root mengikuti config dan environment launcher. DJI juga menyediakan export log melalui Manifold; lokasi detail dapat berubah antar firmware, jadi gunakan [Troubleshooting Manifold](TROUBLESHOOTING_MANIFOLD.md) dan manual resmi firmware target.

## Level Validasi

| Level | Bukti minimum |
|---|---|
| 0 - Static | config/import/test source tanpa hardware |
| 1 - Build | native binary dan dependency check berhasil di Manifold |
| 2 - Ground PSDK | PSDK init, widget, telemetry, dan liveview pada aircraft di tanah |
| 3 - AI runtime | engine load, worker heartbeat, inference/result freshness |
| 4 - DPK lifecycle | package, install, start/stop/restart, dan log melalui jalur DJI |
| 5 - Field | plot nyata, Pilot overlay, finalization, RTK/geospatial, akurasi gap, dan long-run stability |

v27 saat handover baru memiliki source-level validation khusus fix finalizer. v27 belum mencapai Level 1-5.

## Drift Versi yang Harus Diselesaikan

| Indikator | Kategori | Kondisi |
|---|---|---|
| Runtime `00.01.00.26` | **CURRENT RUNTIME** | DPK/runtime terakhir yang terbukti |
| Source canonical v27 | **CURRENT DEVELOPMENT** | Belum menghasilkan artifact |
| `src/psdk/main.cpp` `debugVersion` | **STALE/UNKNOWN** | Tidak konsisten dengan v27 |
| `scripts/build_dpk.sh` `VERSION` | **STALE** | Masih `00.01.00.00` |
| `dpk/app.json.in` `firmware_version` | **STALE** | Masih `00.01.00.00` |
| Startup worker dari launcher dan native | **OPEN** | Berpotensi membuat dua worker |

Sebelum build v27, tetapkan satu version source of truth dan satu ownership startup worker. Perubahan harus direview dan diuji, bukan sekadar mengganti string.

## Gate Sebelum Build/Deploy v27

- [ ] review diff canonical source terhadap provenance v27;
- [ ] tentukan metadata version v27;
- [ ] reconcile `debugVersion`, `app.json`, dan output DPK;
- [ ] reconcile launcher versus native `SpawnPythonWorker()`;
- [ ] pastikan credential lokal tersedia tetapi tidak tercatat;
- [ ] verifikasi PSDK 3.16.0 dan environment target;
- [ ] pilih engine yang hash dan binding-nya sesuai;
- [ ] selesaikan masalah `RegistrationFrameWriter` atau catat dampaknya;
- [ ] build native dan periksa `ldd`;
- [ ] lakukan ground PSDK/liveview/overlay test;
- [ ] validasi AI runtime dan session finalization;
- [ ] buat gate berdasarkan evidence;
- [ ] package DPK dan uji lifecycle;
- [ ] lakukan field validation sebelum menyatakan v27 siap operasi.

## Referensi

- [Initial Setup](INITIAL_SETUP.md)
- [Code Walkthrough](CODE_WALKTHROUGH.md)
- [Model Deployment](MODEL_DEPLOYMENT.md)
- [Validation Evidence](VALIDATION_EVIDENCE.md)
- [Known Issues and Blockers](KNOWN_ISSUES_AND_BLOCKERS.md)
- [Troubleshooting Manifold](TROUBLESHOOTING_MANIFOLD.md)
- [DJI: Build DPK](https://developer.dji.com/doc/payload-sdk-tutorial/en/manifold-quick-start/build-dpk.html)
- [DJI: Manifold Application Management](https://developer.dji.com/doc/payload-sdk-tutorial/en/manifold-quick-start/manifold-platform-capabilities/application-management.html)
- [DJI: Manifold System Tools](https://developer.dji.com/doc/payload-sdk-tutorial/en/manifold-quick-start/manifold-platform-capabilities/system-tools.html)
