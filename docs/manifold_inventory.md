# Inventory Manifold 3

Tanggal percobaan: 31 Juli 2026.

Status: `hardware_blocked`. Tidak ada versi runtime perangkat yang diklaim.

## Pemeriksaan read-only dari Mac

- Interface aktif Mac berada pada jaringan `192.168.97.x`.
- Tidak ada interface/rute langsung `192.168.42.x`.
- Rute ke `192.168.42.120` jatuh ke gateway jaringan biasa.
- Ping ke `192.168.42.120` kehilangan 100% paket.
- TCP port 22 ke `192.168.42.120` kini dapat menerima koneksi TCP, tetapi dua
  percobaan SSH key-only berhenti pada `Connection timed out during banner
  exchange` (timeout 5 dan 15 detik). Karena sesi SSH belum terbentuk,
  inventory perangkat belum dijalankan.
- Tidak ada host alias di `~/.ssh/config`.

Karena itu SSH tidak dijalankan dan semua item berikut tetap `unknown`:
hostname, kernel, architecture, OS, disk, memory, Jetson/platform, CUDA,
cuDNN, TensorRT, Python, compiler, CMake, nvcc, trtexec, OpenCV, FFmpeg,
GStreamer, package PSDK/runtime, `dji_app_ctl`, temperature, power mode,
network perangkat, USB/E-Port, dan waktu sistem.

Angka pada dokumentasi/factory image tidak disalin sebagai inventory aktual.

## Script inventory

`scripts/manifold_inventory_readonly.sh` hanya membaca status dan menyensor
MAC address. Jalankan setelah konektivitas tersedia:

```bash
export MANIFOLD_SSH_TARGET="dji@192.168.42.120"
ssh -o BatchMode=yes -o ConnectTimeout=5 "$MANIFOLD_SSH_TARGET" \
  'bash -s' < manifold_app/scripts/manifold_inventory_readonly.sh \
  > manifold_inventory_raw.txt
```

Sebelum memasukkan hasil ke dokumen ini, hapus hostname/identifier yang tidak
perlu dan jangan sertakan serial number atau credential.

## Satu langkah pemulihan berikutnya

Dengan persetujuan user: matikan aircraft terlebih dahulu; verifikasi dan
kencangkan kabel top E-Port Manifold 3 Kit; hubungkan Mac melalui debug port
aircraft sesuai dokumentasi DJI, bukan USB-C Manifold; kemudian nyalakan
aircraft. Pastikan Mac memperoleh interface `192.168.42.x` sebelum mengulang
ping/SSH. Jangan hot-plug E-Port.

Referensi: [Manifold
development](https://developer.dji.com/doc/payload-sdk-tutorial/en/manifold-quick-start/manifold-development.html),
[hardware environment
setup](https://developer.dji.com/doc/payload-sdk-tutorial/en/manifold-quick-start/development-environment-setup/hardware-environment-setup.html),
dan [Manifold
product](https://developer.dji.com/doc/payload-sdk-tutorial/en/manifold-quick-start/manifold-product.html).
