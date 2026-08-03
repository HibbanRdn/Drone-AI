# Transfer dan instalasi offline

Paket ini berisi source Git aplikasi pada commit `@APP_COMMIT@` dan checkout
resmi Payload SDK tag `@PSDK_TAG@` pada commit `@PSDK_COMMIT@`. Paket tidak
berisi credential, virtual environment, TensorRT engine, dataset, media flight,
atau output inference.

## Transfer dari Mac

Verifikasi sidecar SHA-256 terlebih dahulu, lalu transfer arsip dan sidecar:

```bash
shasum -a 256 -c gap_plot_ai_offline_*.tar.gz.sha256
scp gap_plot_ai_offline_*.tar.gz gap_plot_ai_offline_*.tar.gz.sha256 \
  dji@MANIFOLD_IP:/home/dji/offline-incoming/
```

Tidak ada command SSH yang dijalankan oleh generator. Jalankan langkah berikut
secara manual saat akses Manifold telah disetujui:

```bash
cd /home/dji/offline-incoming
sha256sum -c gap_plot_ai_offline_*.tar.gz.sha256
tar -xzf gap_plot_ai_offline_*.tar.gz
cd gap_plot_ai_offline_*/
./install/verify_offline.sh
./install/install_offline.sh
./install/build_manifold_offline.sh
```

Install default membuat release baru tanpa mengubah source legacy dan tanpa
mengaktifkannya. Setelah build, static overlay, serta ground test lulus, aktivasi
harus eksplisit:

```bash
./install/install_offline.sh --activate
```

Jika release sudah pernah di-install dengan commit yang sama, installer akan
memverifikasi lalu memakai ulang release tersebut tanpa menimpanya.

## Rollback

Installer menyimpan target symlink lama dalam `previous_current.txt` bila ada.
Rollback hanya mengembalikan symlink, tidak menghapus release baru atau legacy:

```bash
ln -sfn "$(cat /home/dji/gap_plot_ai_dev/releases/@APP_COMMIT@/previous_current.txt)" \
  /home/dji/gap_plot_ai_dev/current
```

Jika `current` sebelumnya bukan symlink, installer menolak aktivasi agar source
legacy tidak tertimpa.
