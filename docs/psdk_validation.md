> Catatan handover: dokumen ini adalah dokumen historis sebelum konsolidasi v27. Untuk status canonical terbaru gunakan `README.md`, `HANDOVER.md`, dan dokumen handover utama di `docs/README.md`. Jangan menganggap versi/build/deploy di dokumen ini sebagai status final saat ini.

# Validasi PSDK

## Yang sudah terverifikasi

- Upstream resmi exact tag `3.16.0` terverifikasi pada commit
  `9af08536df3671ed77aee02f1f2e4173b0e0f558`.
- Origin wajib terpetakan ke organisasi resmi `dji-sdk/Payload-SDK`; verifier
  menolak fork/mirror, working tree vendor yang kotor, serta checkout yang
  memiliki submodule atau Git LFS. Tag resmi ini tidak memakai keduanya.
- Build menolak checkout non-exact-tag dan memverifikasi SHA-256 library
  `aarch64-linux-gnu-gcc/libpayloadsdk.a`.
- `main.cpp` lulus `clang++ -std=c++17 -fsyntax-only` terhadap header PSDK
  3.16.0 lokal.
- Konfigurasi koneksi dikunci ke `DJI_USE_ONLY_USB_BULK_DEVICE`; hanya HAL USB
  bulk Manifold 3 yang didaftarkan.
- Native membuat `data/logs` dan direktori IPC sebelum `DjiCore_Init`, termasuk
  saat binary dijalankan langsung.
- Camera source yang dipilih adalah
  `DJI_LIVEVIEW_CAMERA_SOURCE_M4E_VIS`, posisi 1, decoded
  `PIXFMT_RGB_PACKED`.
- `live.input_mode=decoded_rgb` adalah pilihan eksplisit untuk Manifold 3.
  Sample H.264 resmi memakai `DjiLiveview_StartH264Stream` lalu
  `DJICameraStreamDecoder` berbasis FFmpeg 4.x. Decoder sample tersebut tidak
  disalin atau ditautkan ke aplikasi: API decoded Manifold dipakai untuk
  menghindari decode ekstra. Konfigurasi H.264 tercatat tetapi wajib disabled
  sampai dependency target dan hardware path lulus verifikasi.
- Aplikasi memeriksa `DJI_AIRCRAFT_TYPE_M4E` dan mount
  `DJI_MOUNT_POSITION_TYPE_MANIFOLD3_ONBOARD`.
- Callback hanya memvalidasi/copy-replace latest frame. Binary v2 spool,
  telemetry association, metadata, dan optional rendering berjalan di luar
  callback pada `/dev/shm`.
- Telemetry memakai POSITION_FUSED, HEIGHT_RELATIVE, GIMBAL_ANGLES,
  QUATERNION, VELOCITY, GPS_SIGNAL_LEVEL, dan RTK_POSITION_INFO. Nilai yang
  gagal dibaca tetap null.
- Bbox memakai AI recognition metadata dan koordinat 0–10.000. `boxCount`
  resmi `uint8_t`; auto limit 255 dan full count tetap di JSONL.
- Rendered H.264 contour path tersedia di belakang environment gate dan
  default nonaktif; plant metadata memakai normal Pilot liveview.
- Widget config menyediakan Start/Stop Live AI, switches, Snapshot, enam state,
  source/AI FPS, latency/p50, full/sent count, gap N/A, dan error.
- Shutdown menghentikan stream sebelum unregister label/encoder dan deinit;
  callback menolak frame setelah shutdown. Signal handler hanya menetapkan
  `sig_atomic_t`, bukan memanggil mutex/condition variable. Timeout dan interval
  reconnect berasal dari `config/live.yaml`; kegagalan subscription tetap
  terlihat di widget sambil retry terbatas berjalan.
- Identitas portal `ggp-drone-ai` / App ID `189927` divalidasi dari satu file
  lokal ignored. App key dan advanced license berstatus configured/redacted.
- Sample resmi yang dipakai adalah target C++
  `dji_sdk_demo_on_manifold3_cxx`. Header C dan C++ pada repo resmi tidak
  dimodifikasi; build menginjeksi header private generated dengan macro resmi
  `USER_APP_*`.
- Build directory sample default berada di
  `Drone-AI/build/official_psdk_sample_3.16.0`, bukan di checkout DJI, sehingga
  vendor repository tetap clean. Header private sementara distage di `/tmp`
  agar path workspace yang mengandung spasi tidak dipecah oleh upstream CMake.
- App identity buffer divalidasi terhadap `T_DjiUserInfo`: app name 32, App ID
  16, key 32, license 512, developer account 64, dan baud rate 7 byte.

## Yang belum terverifikasi

Status: `psdk_blocked`.

- Inventory OS/toolchain berhasil, tetapi `gap_plot_ai` tidak tercantum pada
  `dji_app_ctl list/status` dan tidak dianggap terpasang.
- Build Linux aarch64 custom belum dijalankan pada Manifold.
- Build dan run official sample pada Manifold.
- PSDK registration/activation nyata.
- Product type dan mount type nyata.
- Telemetry aktual.
- Callback wide liveview dan timestamp aktual.
- Widget muncul di Pilot 2.
- Bbox metadata tampil di Pilot 2.
- H.264 AI-rendered contour tampil di Pilot 2.
- Start/Stop/Snapshot dari Pilot 2.
- Reconnect dan restart tanpa reboot.
- `DJI_DEVELOPER_ACCOUNT` belum tersedia dari screenshot; build perangkat
  tetap diblokir sampai email akun developer diisi lokal.

Marker `runtime/gates/psdk_liveview_verified` tidak dibuat. `run_dev.sh`
menolak berjalan tanpa marker dan secret lokal.

Referensi: [official PSDK
repository](https://github.com/dji-sdk/Payload-SDK), [Liveview
API](https://developer.dji.com/doc/payload-sdk-api-reference/en/practice/liveview.html),
[Liveview AR](https://developer.dji.com/doc/payload-sdk-tutorial/en/function-overview/advanced-function/liveview-ar.html),
dan [Custom
Widget](https://developer.dji.com/doc/payload-sdk-tutorial/en/basic-function/custom-widget.html).

## Input Liveview dan output Pilot 2

Keduanya tidak disamakan:

1. Input model: `DjiLiveview_StartImageStream` menerima RGB packed dari camera
   position 1, source `M4E_VIS`. Alternatif resmi H.264 adalah
   `StartH264Stream` + decoder FFmpeg sample.
2. Overlay normal Pilot: label didaftarkan lalu bbox 0–10.000 dikirim dengan
   `DjiLiveview_SendAiMetaToPilot`. Ini metadata, bukan video publish.
3. Rendered video opsional: frame RGB + metadata di-encode melalui
   `DjiLiveview_EncodeAFrameToH264`; callback encoder meneruskan bitstream lewat
   `DjiPayloadCamera_SendVideoStream`. Ini payload-camera stream terpisah dan
   default-nya nonaktif.

Dengan demikian PSDK 3.16.0 memang menyediakan output AI metadata ke Pilot 2
pada Manifold 3. Namun tampilan aktual pada firmware M4E/Pilot 2 dan batas
operasional object tetap merupakan hardware acceptance, bukan hasil test Mac.
