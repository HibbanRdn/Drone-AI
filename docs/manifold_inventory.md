# Baseline inventory Manifold 3

Tanggal report aktual: 31 Juli 2026.

Sumber baseline adalah output nyata
`runtime/reports/manifold_inventory_raw.txt` dari script read-only melalui SSH
ke `dji@192.168.42.120`. File mentah tetap lokal dan diabaikan Git.

## Fakta terkonfirmasi

| Komponen | Baseline perangkat |
|---|---|
| Arsitektur | Linux `aarch64` |
| OS | Ubuntu 20.04.6 LTS |
| Kernel | `5.10.192-tegra` |
| L4T | R35 revision 5.0 |
| Python | 3.8.10 |
| GCC/G++ | 9.4.0 |
| CMake | 3.16.3 |
| CUDA Toolkit | 11.4 terpasang menurut package metadata |
| cuDNN | 8.6.0.166 |
| TensorRT | 8.5.2, CUDA 11.4 |
| FFmpeg | 4.2.7 NVIDIA |
| GStreamer | 1.16.3 |
| OpenCV | 4.2 di `/lib/aarch64-linux-gnu` dan 4.5 di `/usr/local/lib` |
| Root filesystem | sekitar 8,7 GiB kosong dari 27 GiB |
| RAM | 14 GiB, sekitar 12 GiB available saat inventory |
| DJI app tool | `/system/bin/dji_app_ctl` |
| Interface internal | `rndis0` dengan `192.168.42.2/24` |

Inventory dijalankan dari laptop melalui endpoint SSH `192.168.42.120`.
Alamat `.2` adalah alamat interface yang terlihat dari dalam Manifold. Report
belum cukup untuk membuktikan detail routing/NAT di antara kedua alamat, jadi
endpoint dokumentasi tetap `.120`.

`dji_app_ctl list/status` hanya menampilkan aplikasi resmi DJI
`Smart3DExplore` versi `00.01.00.16`, platform `manifold3`, status
`Not Running`. Custom app `gap_plot_ai` belum terlihat dan tidak boleh
dianggap sudah terpasang. Inventory tidak menjalankan, menghentikan,
menginstal, menghapus, atau mengubah aplikasi tersebut.

## Koreksi terhadap output report lama

- `nvcc: command not found` hanya membuktikan `nvcc` tidak ditemukan melalui
  `PATH` saat itu. Package `cuda-nvcc-11-4` terpasang, tetapi executable
  `/usr/local/cuda/bin/nvcc` atau `/usr/local/cuda-11.4/bin/nvcc` belum
  dikonfirmasi oleh report lama.
- `trtexec --version` bukan probe versi yang valid pada instalasi ini.
  Banner `TensorRT v8502` dan package metadata mengonfirmasi TensorRT 8.5.2;
  error `Model missing or format not recognized` berasal dari pemanggilan
  `trtexec` tanpa model, bukan bukti runtime rusak.
- Dua library OpenCV terlihat di linker cache, tetapi report lama belum
  membuktikan mana yang dipilih `pkg-config`, CMake, atau modul Python `cv2`.

Script inventory baru memeriksa kandidat path `nvcc`, package/header
TensorRT tanpa memanggil `trtexec --version`, pilihan OpenCV melalui
`pkg-config`, serta versi/lokasi package Python `numpy`, `cv2`, `onnx`,
`tensorrt`, `torch`, dan `ultralytics`. Semua probe bersifat read-only dan
tahan terhadap komponen yang tidak tersedia.

## Dampak pada project

- Native frontend tetap C++17, CMake minimum 3.16, GCC 9-compatible, Linux
  aarch64, dan memakai library resmi PSDK 3.16.0
  `aarch64-linux-gnu-gcc/libpayloadsdk.a`.
- Native frontend sengaja tidak menautkan OpenCV. Pemilihan OpenCV hanya
  terjadi pada environment Python dan harus mengarah konsisten ke satu
  instalasi.
- Source Python target mendukung Python 3.8. Dependency berat development
  host tidak boleh dipasang otomatis pada Manifold; wheel target harus cocok
  dengan Python 3.8, aarch64, JetPack/L4T, CUDA 11.4, dan TensorRT 8.5.2.
- Build engine tidak memerlukan `nvcc` karena memakai TensorRT runtime.
  Jika pekerjaan CUDA custom nantinya memerlukan compiler, gunakan path yang
  telah dibuktikan, misalnya:

  ```bash
  export CUDACXX=/usr/local/cuda-11.4/bin/nvcc
  ```

  Jangan membuat symlink atau mengubah `PATH` perangkat hanya untuk audit.
- ONNX adalah format pertukaran. File `.engine` harus dibangun pada Manifold
  ini atau environment Linux aarch64/GPU/TensorRT/CUDA yang identik. Engine
  dari macOS, Windows x86, GPU lain, atau TensorRT lain tidak portable.
- Script build, export, engine, dan runtime memeriksa ruang kosong. Default
  minimum adalah 1.024 MiB untuk artefak dan 1.536 MiB untuk runtime.
  Runtime tetap memiliki cap session dan rotasi log/JSONL.

## Yang belum terkonfirmasi

- model device dari `/proc/device-tree/model`;
- target symlink `/usr/local/cuda` dan lokasi executable `nvcc`;
- versi/lokasi aktif `cv2` dan package Python AI lainnya;
- build PSDK, activation, liveview, widget, dan pipeline AI pada perangkat;
- kompatibilitas wheel target dan TensorRT engine;
- detail routing/NAT `.120` dan `.2`;
- packaging, instalasi, atau startup DPK custom.

Ambil ulang inventory dengan script yang diperbarui sebelum instalasi
dependency atau build perangkat:

```bash
ssh -o ConnectTimeout=30 dji@192.168.42.120 \
  'bash -s' < scripts/manifold_inventory_readonly.sh \
  | tee runtime/reports/manifold_inventory_raw.txt
```

Audit inventory tidak membutuhkan penyalinan script ke Manifold dan tidak
memerlukan `sudo`.
