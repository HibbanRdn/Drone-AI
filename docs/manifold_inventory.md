# Baseline inventory Manifold 3

Tanggal report aktual: 31 Juli 2026.

Sumber baseline terbaru adalah output nyata
`runtime/reports/manifold_inventory_raw2.txt` dari script read-only melalui
SSH ke `dji@192.168.42.120`. Report mentah tetap lokal dan diabaikan Git.

## Fakta terkonfirmasi

| Komponen | Baseline perangkat |
|---|---|
| Model device tree | NVIDIA Orin NX Developer Kit |
| Arsitektur | Linux `aarch64` |
| OS | Ubuntu 20.04.6 LTS |
| Kernel | `5.10.192-tegra` |
| L4T | R35 revision 5.0 |
| Python | 3.8.10 |
| GCC/G++ | 9.4.0 |
| CMake | 3.16.3 |
| CUDA | 11.4, compiler V11.4.315 |
| CUDA default | `/usr/local/cuda` → `/usr/local/cuda-11.4` |
| nvcc | Ada di `/usr/local/cuda/bin/nvcc` dan `/usr/local/cuda-11.4/bin/nvcc` |
| cuDNN | 8.6.0.166 |
| TensorRT | 8.5.2.2, CUDA 11.4 |
| trtexec | `/usr/src/tensorrt/bin/trtexec` |
| FFmpeg | 4.2.7 NVIDIA |
| GStreamer | 1.16.3 |
| Root filesystem | sekitar 8,7 GiB kosong dari 27 GiB |
| RAM | 14 GiB, sekitar 12 GiB available saat inventory |
| Swap | 28 GiB, belum digunakan saat inventory |
| DJI app tool | `/system/bin/dji_app_ctl` |
| Interface internal | `rndis0` dengan `192.168.42.2/24` |

`nvcc` tidak berada pada `PATH`, tetapi kedua path eksplisit dapat dieksekusi.
Build saat ini tidak mengompilasi custom CUDA sehingga tidak membutuhkan
`nvcc`. Jika custom CUDA ditambahkan nanti, gunakan
`CUDACXX=/usr/local/cuda-11.4/bin/nvcc` secara eksplisit tanpa mengubah
symlink atau `PATH` perangkat.

Inventory dijalankan dari laptop melalui endpoint SSH `192.168.42.120`.
Alamat `.2` adalah interface yang terlihat dari dalam Manifold. Report belum
membuktikan detail routing/NAT di antara keduanya, sehingga endpoint laptop
tetap `.120`.

## OpenCV dan Python AI

Linker cache memuat OpenCV 4.2 dari `/lib/aarch64-linux-gnu` dan OpenCV 4.5
dari `/usr/local/lib`. Tidak ada file `opencv4.pc` maupun `opencv.pc`, sehingga
CMake/pkg-config tidak dapat memilih instalasi secara otomatis.

Project menghindari konflik ini sebagai berikut:

- frontend native C++ tidak menautkan OpenCV;
- Python 3.8 mengimpor `cv2` 4.5.4 dari
  `/usr/local/lib/python3.8/dist-packages/cv2`;
- preflight runtime menolak `cv2` 4.2 atau lokasi Python selain `/usr/local`.

Package Python yang terkonfirmasi:

| Package | Status |
|---|---|
| NumPy | 1.17.4, `/usr/lib/python3/dist-packages` |
| OpenCV/cv2 | 4.5.4, `/usr/local/lib/python3.8/dist-packages` |
| TensorRT | 8.5.2.2, `/usr/lib/python3.8/dist-packages` |
| ONNX | belum terpasang |
| PyTorch | belum terpasang |
| Ultralytics | belum terpasang |

Runtime live direct TensorRT tidak mengimpor PyTorch/Ultralytics/ONNX. NumPy,
OpenCV, TensorRT, CUDA dan cuDNN berasal dari system image; hanya PyYAML yang
dipasang dari wheel CPython 3.8 Linux aarch64 dengan SHA-256 terkunci. Export,
rebuild engine dan Ultralytics tetap di luar deployment live ini.

## Status aplikasi DJI

`dji_app_ctl list/status` hanya menampilkan aplikasi resmi DJI
`Smart3DExplore` versi `00.01.00.16`, platform `manifold3`, status
`Not Running`. Custom app `gap_plot_ai`/`ggp-drone-ai` belum terpasang.
Inventory tidak menjalankan, menghentikan, menginstal, menghapus, atau
mengubah aplikasi apa pun.

## Interpretasi probe

- `nvcc_on_path=unavailable` bukan error CUDA; executable sudah terbukti pada
  path eksplisit.
- `opencv_pkg_config_missing` bukan bukti OpenCV tidak tersedia. Ini berarti
  CMake tidak boleh memilih OpenCV secara implisit.
- `nvpmodel_config=unavailable` bukan bukti tool hilang; executable
  `/usr/sbin/nvpmodel` tersedia.
- Probe TensorRT terbaru menggunakan package metadata dan header, sehingga
  tidak menghasilkan false error `Model missing or format not recognized`.

## Status kesiapan

Source native cocok dengan Linux aarch64, CMake 3.16.3, GCC 9.4, dan library
PSDK 3.16.0. Build perangkat belum dijalankan.

Belum siap untuk runtime AI atau DPK formal karena:

- ONNX, PyTorch, dan Ultralytics target belum tersedia;
- belum ada TensorRT engine yang dibangun/divalidasi pada target;
- official PSDK sample/liveview/widget belum diuji;
- versi firmware package Manifold untuk `ver_min`/`ver_max` belum diketahui;
- worker Python memakai dependency dinamis, sedangkan DPK DJI mewajibkan
  third-party dependency statis;
- package staging lengkap dan rollback artifact belum ada.

Lihat `docs/manifold_readiness.md` untuk gate dan command yang boleh dijalankan
nanti. Ambil ulang inventory setelah dependency target disiapkan:

```bash
ssh -o ConnectTimeout=30 dji@192.168.42.120 \
  'bash -s' < scripts/manifold_inventory_readonly.sh \
  | tee runtime/reports/manifold_inventory_raw3.txt
```

Audit inventory tidak membutuhkan penyalinan script ke Manifold dan tidak
memerlukan `sudo`.
