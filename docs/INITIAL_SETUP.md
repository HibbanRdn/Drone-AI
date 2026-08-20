# Initial Setup DJI Matrice 4E, RC Plus 2, dan Manifold 3

Dokumen ini memandu setup dari perangkat baru sampai Manifold 3 siap untuk development Drone AI Gap Plot. Prosedur firmware mengacu pada dokumentasi resmi DJI yang diperiksa pada **2026-08-20**. Nomor versi dapat berubah; selalu periksa Download Center dan release notes sebelum memulai.

## Cara Membaca Status Informasi

Dokumen ini memakai empat label:

- **DJI OFFICIAL REQUIREMENT**: dinyatakan oleh manual, release notes, atau dokumentasi developer DJI.
- **PROJECT REQUIREMENT**: diwajibkan oleh source atau script repository ini.
- **PROJECT RECOMMENDATION**: urutan konservatif project ketika DJI tidak memberi urutan lintas semua perangkat secara eksplisit.
- **UNKNOWN / VERIFY ON DEVICE**: detail belum dibuktikan; jangan diisi dengan asumsi.

## Hasil Akhir Yang Diharapkan

Setup selesai jika:

```text
Matrice 4E dan RC linked
  -> firmware aircraft/RC/Pilot selaras
  -> Manifold dipasang dan firmware-nya kompatibel
  -> akses SSH/VNC tersedia
  -> PSDK 3.16.0 terverifikasi
  -> repository dan model portable tersedia
  -> credential lokal tersedia tanpa masuk Git
  -> siap melakukan build v27
```

Setup ini **tidak** berarti v27 sudah dibangun, dipackage, atau diuji pada hardware.

## 1. Inventory Hardware dan Akun

### 1.1 Perangkat wajib

- [ ] DJI Matrice 4E, bukan Matrice 4T.
- [ ] DJI RC Plus 2 Enterprise.
- [ ] DJI Manifold 3.
- [ ] Manifold 3 Accessory Kit khusus Matrice 4 Series: mounting bracket, coaxial cable, screw, dan tool terkait.
- [ ] Baterai aircraft yang sehat dan terisi penuh untuk firmware update.
- [ ] Charger aircraft dan charger RC yang sesuai.
- [ ] microSD card atau USB storage yang dapat dibaca RC.
- [ ] Kabel USB-C data yang baik.
- [ ] Windows PC untuk offline system firmware update Manifold 3.
- [ ] Host development dengan Git dan Git LFS.
- [ ] Internet untuk mengunduh package resmi dan aktivasi awal.

DJI mencantumkan microSD U3/Class 10/V30 atau lebih tinggi untuk RC Plus 2. DJI tidak menetapkan filesystem khusus pada prosedur offline firmware yang dirujuk dokumen ini. Gunakan media yang sudah dapat dibaca oleh RC; bila perlu, format dari perangkat DJI dan jangan menebak filesystem secara manual.

### 1.2 Akun dan akses

- [ ] Akun DJI yang berwenang untuk aktivasi dan DJI Assistant 2.
- [ ] Akses ke DJI Developer User Center untuk aplikasi PSDK `ggp-drone-ai`.
- [ ] Hak akses GitHub repository.
- [ ] Credential PSDK diperoleh melalui jalur internal yang berwenang.

Nilai App Key, Advanced License, account, token, atau password tidak boleh ditulis ke dokumentasi atau Git. Template repository ada di `config/dji_sdk_app_info.example.h`; nilai lokal berada di file ignored `config/dji_sdk_app_info.local.h`.

## 2. Port dan Topologi Yang Relevan

### 2.1 Manifold 3

Menurut Manifold 3 User Manual:

- **E-Port Manifold 3** digunakan untuk koneksi ke aircraft yang kompatibel.
- **USB-C Manifold 3** mendukung USB 3.0 device dan USB-PD power, tetapi **tidak mendukung koneksi komputer langsung**.
- Untuk firmware/file access melalui komputer, Manifold dipasang pada aircraft lalu komputer dihubungkan ke **aircraft debugging port**.
- Tombol reset yang ditahan 10 detik melakukan factory reset dan menghapus aplikasi/data. Jangan digunakan sebagai troubleshooting biasa.

Posisi fisik mounting, debug port, dan routing coaxial cable harus mengikuti diagram **Manifold 3 User Manual bagian 2.2 Matrice 4 Series**. Dokumentasi project tidak menggantikan diagram mekanis DJI.

### 2.2 Topologi development kabel

```text
Windows/Linux PC
    | USB-C data
aircraft debugging port
    | internal aircraft/E-Port path
Manifold 3
```

Untuk Matrice 4E/4T/4D/4TD dengan Manifold pada E-Port, dokumentasi PSDK mencantumkan direct-debug SSH address `192.168.42.120`. Alamat ini adalah alamat target device, bukan alamat universal pada jaringan eksternal.

## 3. Prinsip Compatibility Firmware

```text
Aircraft firmware
    <-> RC firmware + DJI Pilot 2
    <-> Manifold 3 firmware
    <-> Payload SDK
    <-> custom DPK application
```

Alasannya:

- Aircraft/RC/Pilot yang tertinggal dapat kehilangan capability atau menampilkan mismatch warning.
- Dukungan Manifold 3 untuk PSDK application pada Matrice 4 Series baru dinyatakan pada kombinasi firmware akhir 2025 dan sesudahnya.
- Manifold 3 release notes mengikat firmware Manifold ke versi PSDK dan versi aircraft/RC/Pilot tertentu.
- PSDK 3.16.0 menyebut perbaikan high-power Manifold 3 yang baru efektif setelah firmware Manifold diperbarui.
- DPK memakai `ver_min`/`ver_max`; rentang salah dapat menolak install atau memberi kesan kompatibilitas palsu.
- TensorRT engine dapat perlu dibangun ulang ketika firmware mengubah CUDA/TensorRT/JetPack.

Jangan memilih kombinasi hanya karena pernah bekerja pada snapshot lama. Pilih satu release family dari release notes resmi, catat seluruh versinya, dan validasi bersama.

## 4. Versi Resmi Saat Pemeriksaan

Terakhir diverifikasi: **2026-08-20**.

| Komponen | Paket terbaru yang ditemukan | Tanggal vendor | Sumber |
|---|---:|---:|---|
| Matrice 4E offline firmware | `17.02.05.01` | 2026-06-16 | Matrice 4 Series Download Center |
| RC Plus 2 Enterprise offline firmware | `01.64.08.20` | 2026-07-10 | Matrice 4 Series Download Center |
| DJI Pilot 2 pada release family tersebut | `17.2.5.28` | 2026-07-10 | Matrice 4 Series Release Notes |
| Manifold 3 offline firmware | `17.00.01.01` | 2026-03-31 | Manifold 3 Download Center |
| DJI Assistant 2 Enterprise Series | `2.1.20` | 2026-03-06 download | Manifold 3 Download Center |
| Payload SDK | `3.16.0` | 2026-03-31/04-01 | DJI Developer / official GitHub release |

Versi Manifold `17.00.01.01` release notes memasangkan PSDK `3.16.0`, M4 Series `17.00.00.01`, RC `01.64.08.06`, dan Pilot `17.0.0.40`. Aircraft/RC kemudian memiliki release lebih baru. Karena release notes Manifold memerintahkan update perangkat lain terlebih dahulu lalu Manifold, gunakan versi terbaru aircraft/RC/Pilot yang kompatibel dan tetap lakukan smoke test PSDK setelah semua update.

## 5. Recommended Initial Firmware Update Order

> Status: gabungan DJI OFFICIAL REQUIREMENT dan PROJECT RECOMMENDATION.

DJI tidak menerbitkan satu urutan lintas RC, aircraft, dan Manifold dalam satu prosedur. DJI menyatakan aircraft/RC/Pilot harus terbaru dan release notes Manifold menyatakan perangkat lain diperbarui lebih dahulu, kemudian Manifold. Project memakai urutan berikut:

```text
STEP 0  Download package + release notes; catat hash lokal
STEP 1  Aktivasi/link perangkat baru sesuai DJI Pilot 2
STEP 2  Update RC Plus 2 / DJI Pilot environment
STEP 3  Update Matrice 4E
STEP 4  Restart; verifikasi aircraft <-> RC tanpa mismatch
STEP 5  Pasang Manifold sesuai manual
STEP 6  Update Manifold 3 terakhir melalui Windows DJI Assistant 2
STEP 7  Restart seluruh perangkat; verifikasi versi dan akses
STEP 8  Verifikasi PSDK 3.16.0 dan environment project
```

Alasan RC didahulukan adalah agar UI updater/Pilot yang menjalankan offline workflow sudah berada pada release family target. Ini **PROJECT RECOMMENDATION**, bukan urutan eksplisit DJI. Jika release notes terbaru memberi urutan berbeda, ikuti release notes terbaru.

## 6. Step 0 - Download dan Audit Package

1. Buka Matrice 4 Series Download Center.
2. Unduh **Matrice 4E** offline firmware. Jangan mengambil package M4T.
3. Unduh RC Plus 2 Enterprise offline firmware.
4. Unduh Matrice 4 Series Release Notes terbaru.
5. Buka Manifold 3 Download Center.
6. Unduh Manifold 3 offline firmware dan release notes.
7. Unduh DJI Assistant 2 Enterprise Series **Windows**.
8. Unduh/checkout Payload SDK official tag `3.16.0` dari `dji-sdk/Payload-SDK`.
9. Simpan package vendor di luar repository ini.
10. Catat nama file, versi, tanggal download, ukuran, dan SHA-256 lokal.

Contoh checksum pada macOS/Linux:

```bash
shasum -a 256 <downloaded-file>
```

Contoh checksum pada PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 <downloaded-file>
```

DJI tidak mempublikasikan checksum pada halaman download yang dirujuk. Checksum lokal tetap berguna untuk memastikan file yang dipindahkan tidak berubah; ia bukan signature vendor.

## 7. Offline Update RC dan Matrice 4E

### 7.1 Persiapan storage

1. Pastikan media terbaca dari Files/DJI Pilot 2 pada RC.
2. Salin package aircraft dan RC yang benar ke external storage.
3. Pertahankan nama dan bentuk package hasil download.
4. Jangan mengekstrak atau mengganti struktur archive kecuali README di dalam package DJI secara eksplisit memerintahkannya.
5. Jangan mencampur package M4E dan M4T.

DJI User Manual hanya menyatakan package offline diletakkan pada microSD/USB storage dan dipilih dari DJI Pilot 2. Manual tidak menetapkan folder root/subfolder, filesystem, atau prosedur ekstraksi. Karena itu tiga detail tersebut harus dianggap **UNKNOWN / VERIFY IN CURRENT PACKAGE**, bukan diisi dengan tebakan.

### 7.2 Update RC Plus 2

> DJI OFFICIAL: aircraft dan RC harus fully charged sebelum update; restart setelah update.

1. Pastikan RC memiliki daya penuh dan tidak akan kehilangan power.
2. Masukkan microSD atau USB storage ke RC.
3. Nyalakan RC dan buka DJI Pilot 2.
4. Masuk ke `HMS > Firmware Update > Offline Update`.
5. Pilih package **RC Plus 2 Enterprise** dari external storage.
6. Periksa ulang product name dan versi sebelum menekan update.
7. Tekan `Update All` sesuai UI DJI.
8. Jangan melepas storage atau mematikan RC selama proses.
9. Tunggu update dan restart selesai.
10. Buka kembali halaman firmware dan catat versi RC serta versi DJI Pilot 2.

Jika UI tidak mengenali package, jangan mengubah extension. Unduh ulang dari Download Center, cek media dapat dibaca, dan periksa petunjuk dalam package/release notes.

### 7.3 Update Matrice 4E

1. Pasang baterai aircraft yang penuh.
2. Nyalakan RC dan aircraft; pastikan keduanya linked.
3. Biarkan external storage berisi package M4E pada RC.
4. Buka `HMS > Firmware Update > Offline Update`.
5. Pilih package **Matrice 4E**, bukan M4T.
6. Periksa target product dan versi.
7. Tekan `Update All`.
8. Jauhkan aircraft dari orang/hewan; gimbal, indicator, dan reboot dapat berubah selama update.
9. Jangan mematikan aircraft/RC atau mencabut storage.
10. Setelah sukses, power-cycle aircraft dan RC.
11. Periksa firmware aircraft, RC, DJI Pilot 2, dan accessory warning.

DJI menyatakan perangkat yang terpasang pada aircraft dapat ikut diperbarui. Setelah update, cek accessory status dan ulangi update untuk setiap baterai bila release notes terbaru menyatakan battery firmware termasuk aircraft firmware.

### 7.4 Jika update gagal

DJI menyarankan restart aircraft, RC, dan DJI Pilot 2/DJI Assistant 2 lalu mencoba kembali. Sebelum retry, pastikan power, package product, storage, dan checksum file transfer benar.

## 8. Manifold 3 Offline Firmware Update

### 8.1 Requirement

> DJI OFFICIAL REQUIREMENT: offline system firmware update Manifold 3 hanya didukung oleh DJI Assistant 2 versi Windows. Mac version tidak boleh dianggap setara untuk langkah ini.

- Windows PC.
- DJI Assistant 2 Enterprise Series terbaru; saat pemeriksaan `2.1.20`.
- Manifold 3 offline firmware ZIP terbaru; saat pemeriksaan `17.00.01.01`.
- Manifold dipasang pada Matrice 4E dengan accessory kit yang benar.
- Kabel USB data dari Windows PC ke aircraft debugging port.
- Aircraft dan Manifold mendapat power stabil.
- Akun DJI untuk login DJI Assistant 2.

Release notes memperkirakan update dapat melebihi 30 menit dan melarang pemutusan power/koneksi selama update.

### 8.2 Connection

1. Matikan aircraft sebelum pekerjaan mekanis.
2. Pasang bracket dan Manifold sesuai diagram manual bagian Matrice 4 Series.
3. Hubungkan E-Port/coaxial cable sesuai accessory kit.
4. Nyalakan aircraft agar Manifold mendapat power.
5. Hubungkan Windows PC ke **aircraft debugging port** dengan kabel USB data.

Jangan menghubungkan PC langsung ke USB-C Manifold; manual menyatakan port itu tidak mendukung direct computer connection.

### 8.3 Update melalui DJI Assistant 2

1. Install DJI Assistant 2 Enterprise Series Windows dari situs resmi.
2. Jalankan dan login dengan akun DJI yang berwenang.
3. Pastikan aircraft/Manifold terdeteksi.
4. Pilih `Offline Update`.
5. Pilih device Manifold 3 dan firmware package yang telah diunduh.
6. Periksa versi target.
7. Tekan `Start Update`.
8. Jangan mencabut USB, power, atau mematikan aircraft.
9. Amati LED Manifold: yellow fast blink menandakan firmware update; slow red menandakan gagal; slow green menandakan normal/update complete menurut manual.
10. Tunggu prompt berhasil, lalu lakukan power cycle terkontrol.

Nama tombol dapat sedikit berubah antar build DJI Assistant. Jika tidak ada `Offline Update`, pastikan menggunakan Windows build dan device terdeteksi sebagai Manifold 3.

### 8.4 Verification read-only

Setelah reboot dan SSH tersedia:

```bash
uname -a
uname -m
cat /etc/os-release
python3 --version
gcc --version
g++ --version
cmake --version
df -h
command -v dji_app_ctl
dji_app_ctl list
dji_app_ctl status
```

Expected minimum:

- architecture `aarch64`;
- Manifold boot normal;
- storage tidak penuh/read-only;
- `dji_app_ctl` tersedia;
- app list/status dapat dibaca.

Versi firmware Manifold harus dicek di DJI Pilot 2/DJI Assistant 2 karena tidak ada satu command shell canonical yang didokumentasikan DJI untuk menampilkan nomor firmware system package.

## 9. Post-Firmware Compatibility Check

- [ ] RC boot normal.
- [ ] DJI Pilot 2 boot normal.
- [ ] Aircraft linked ke RC.
- [ ] Aircraft menunjukkan product Matrice 4E.
- [ ] Aircraft firmware sesuai release target.
- [ ] RC firmware dan Pilot sesuai release target.
- [ ] Tidak ada firmware mismatch warning.
- [ ] Aircraft dan RC sudah power-cycle.
- [ ] Manifold dipasang dengan accessory kit Matrice 4 Series.
- [ ] Manifold LED normal dan boot selesai.
- [ ] Manifold firmware sesuai release target.
- [ ] Windows DJI Assistant 2 dapat mendeteksi device.
- [ ] SSH tersedia.
- [ ] `uname -m` menghasilkan `aarch64`.
- [ ] `dji_app_ctl list/status` berjalan.
- [ ] Storage cukup dan filesystem writable.
- [ ] PSDK 3.16.0 official checkout tersedia.

## 10. Access Manifold

### 10.1 Direct USB debug SSH

Dengan Manifold terpasang pada M4E E-Port dan PC terhubung ke aircraft debug port:

```bash
ssh <user>@192.168.42.120
```

Gunakan username/password yang diberikan administrator perangkat atau proses provisioning resmi. Jangan menyimpan password dalam shell script, README, atau Git.

Jika memakai USB Ethernet/Wi-Fi adapter melalui docking station Manifold, cari IP pada router/network management page lalu gunakan:

```bash
ssh <user>@<manifold-ip>
```

### 10.2 VNC

Dokumentasi DJI menyediakan VNC web client:

```text
http://<manifold-ip>:6080/vnc.html
```

SSH tetap direkomendasikan untuk build/debug; VNC dipakai bila GUI diperlukan.

### 10.3 File transfer

Pilihan resmi:

- SCP/SFTP untuk developer;
- Samba melalui DJI Assistant 2 pada Windows;
- export data aplikasi ke USB storage melalui DJI Pilot 2.

Project merekomendasikan SCP/SFTP untuk source/package terverifikasi:

```bash
scp <local-file> <user>@<manifold-ip>:<remote-directory>/
```

Jangan menyalin `.venv` host x86/macOS ke Manifold.

### 10.4 App management

Command read-only awal:

```bash
dji_app_ctl list
dji_app_ctl status
```

`dji_app_ctl` mengelola DPK. Ia berbeda dari `install.py`, yang pada firmware/PSDK baru dipakai untuk instalasi dependency rootless (`apt`/`deb`). Detail ada di `BUILD_DEPLOYMENT.md`.

## 11. Environment Development

### 11.1 Known working project environment

Snapshot project merekam:

```text
Ubuntu 20.04.6
aarch64 / NVIDIA Orin NX
Python 3.8.10
GCC/G++ 9.4.0
CMake 3.16.3
CUDA 11.4
TensorRT 8.5.2.2
OpenCV 4.5.4
PSDK 3.16.0
```

Ini adalah **KNOWN WORKING PROJECT ENVIRONMENT**, bukan requirement universal untuk semua firmware masa depan.

### 11.2 Latest DJI guidance

DJI menyatakan Manifold 3 sudah membawa environment PSDK dan mendukung build langsung pada device atau cross-compile dengan `aarch64-linux-gnu-gcc/g++`. DJI juga memperingatkan bahwa compiler cross-build yang lebih baru dari GCC 9.4 dapat menimbulkan compatibility issue.

Setelah firmware update, inventaris ulang:

```bash
python3 --version
gcc --version
g++ --version
cmake --version
nvcc --version
python3 -c 'import tensorrt as trt; print(trt.__version__)'
python3 -c 'import cv2; print(cv2.__version__)'
```

Jika CUDA/TensorRT/GPU runtime berbeda dari manifest, jangan memakai engine lama sebelum read-only deserialization validation. Lihat `MODEL_DEPLOYMENT.md`.

## 12. PSDK dan Repository Setup

### 12.1 Repository

Pada development host:

```bash
git clone https://github.com/HibbanRdn/Drone-AI.git
cd Drone-AI
git branch --show-current
git lfs install
git lfs pull
python3 scripts/model_preflight.py
```

Expected branch default/canonical:

```text
feature/pilot-liveview-inference
```

### 12.2 PSDK

Checkout official `dji-sdk/Payload-SDK` tag `3.16.0` di luar repository ini. Verifikasi dengan:

```bash
python3 scripts/verify_psdk_316.py <path-to-Payload-SDK-3.16.0>
```

Project CMake juga memverifikasi SHA-256 `libpayloadsdk.a` aarch64 official 3.16.0.

### 12.3 Credential lokal

```bash
cp config/dji_sdk_app_info.example.h config/dji_sdk_app_info.local.h
```

Isi hanya pada mesin yang berwenang. Validasi struktur tanpa menampilkan nilai:

```bash
python3 scripts/psdk_app_info.py config/dji_sdk_app_info.local.h
```

### 12.4 Ready-for-build gate

```bash
export PSDK_ROOT=<path-to-official-Payload-SDK-3.16.0>
./scripts/build_dpk.sh --check
```

`--check` hanya menampilkan dependency/gate; tidak membangun DPK.

## 13. Initial Setup Checklist - Drone + RC + Manifold Baru

### A. Hardware

- [ ] Matrice 4E tersedia dan product label diverifikasi.
- [ ] RC Plus 2 Enterprise tersedia.
- [ ] Manifold 3 tersedia.
- [ ] Accessory kit khusus Matrice 4 Series lengkap.
- [ ] Baterai/charger/kabel USB data tersedia.
- [ ] microSD/USB storage dikenali RC.
- [ ] Windows PC tersedia.

### B. Firmware package

- [ ] M4E package diunduh dari Download Center.
- [ ] Package RC diunduh.
- [ ] Manifold package diunduh.
- [ ] DJI Assistant 2 Windows diunduh.
- [ ] Release notes aircraft dan Manifold dibaca.
- [ ] Versi/tanggal/hash lokal dicatat.
- [ ] Package M4T tidak tercampur.

### C. Firmware update

- [ ] RC/Pilot diperbarui.
- [ ] RC direstart dan versinya dicatat.
- [ ] Aircraft M4E diperbarui.
- [ ] Aircraft dan RC direstart.
- [ ] Tidak ada mismatch warning.
- [ ] Manifold dipasang sesuai manual.
- [ ] Windows PC terhubung ke aircraft debugging port.
- [ ] Manifold diperbarui terakhir.
- [ ] Manifold direstart dan versinya dicatat.

### D. Compatibility

- [ ] Aircraft linked ke RC.
- [ ] DJI Pilot 2 normal.
- [ ] Manifold boot normal.
- [ ] Firmware family dicatat sebagai satu baseline.
- [ ] PSDK 3.16.0 dipilih dan release notes diperiksa.

### E. Manifold access

- [ ] SSH berhasil tanpa menyimpan password di repo.
- [ ] VNC diuji bila diperlukan.
- [ ] SCP/SFTP tersedia.
- [ ] `dji_app_ctl list` berhasil.
- [ ] `dji_app_ctl status` berhasil.
- [ ] Disk space dan filesystem normal.

### F. Environment

- [ ] OS/architecture dicatat.
- [ ] Python/GCC/G++/CMake dicatat.
- [ ] CUDA/TensorRT/OpenCV dicatat.
- [ ] Perbedaan dari known working environment dievaluasi.

### G. PSDK dan repository

- [ ] Repository canonical berhasil di-clone.
- [ ] Branch canonical benar.
- [ ] Git LFS artifact sudah ditarik.
- [ ] `model_preflight.py` PASS.
- [ ] Official PSDK 3.16.0 tersedia.
- [ ] `verify_psdk_316.py` PASS.
- [ ] Credential lokal diperoleh melalui jalur berwenang.
- [ ] Credential tidak tracked Git.

### H. Ready for build

- [ ] `PSDK_ROOT` menunjuk checkout official.
- [ ] Target build adalah Linux aarch64.
- [ ] Model engine policy dipahami.
- [ ] Version metadata belum diubah tanpa release decision.
- [ ] Developer lanjut ke `MODEL_DEPLOYMENT.md` dan `BUILD_DEPLOYMENT.md`.

## Referensi Resmi

Terakhir diverifikasi: **2026-08-20**.

- [DJI Matrice 4 Series Download Center](https://enterprise.dji.com/matrice-4-series/downloads)
- [DJI Matrice 4 Series Support/FAQ](https://www.dji.com/support/product/matrice-4-series)
- [DJI Matrice 4 Series User Manual](https://dl.djicdn.com/downloads/DJI_Matrice_4_Series/DJI_Matrice_4_Series_User_Manual_en.pdf)
- [DJI Matrice 4 Series Release Notes](https://enterprise.dji.com/matrice-4-series/downloads)
- [DJI Manifold 3 Download Center](https://enterprise.dji.com/manifold-3/downloads)
- [DJI Manifold 3 User Manual v1.2](https://dl.djicdn.com/downloads/Manifold_3/20250828/Manifold_3_User_Manual_v1.2_en.pdf)
- [DJI Manifold 3 Release Notes](https://dl.djicdn.com/downloads/Manifold_3/RN/20260331/Manifold_3_Release_Notes_en.pdf)
- [PSDK Software Environment Setup](https://developer.dji.com/doc/payload-sdk-tutorial/en/manifold-quick-start/development-environment-setup/software-environment-setup.html)
- [PSDK Hardware Environment Setup](https://developer.dji.com/doc/payload-sdk-tutorial/en/manifold-quick-start/development-environment-setup/hardware-environment-setup.html)
- [Official DJI Payload-SDK releases](https://github.com/dji-sdk/Payload-SDK/releases)
