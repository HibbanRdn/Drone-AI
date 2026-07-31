# Validasi PSDK

## Yang sudah terverifikasi

- Upstream resmi exact tag `3.16.0` terverifikasi pada commit
  `9af08536df3671ed77aee02f1f2e4173b0e0f558`.
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
- Aplikasi memeriksa `DJI_AIRCRAFT_TYPE_M4E` dan mount
  `DJI_MOUNT_POSITION_TYPE_MANIFOLD3_ONBOARD`.
- Latest-frame spool bounded: frame pending lama diganti dan counter drop
  dinaikkan.
- Telemetry memakai POSITION_FUSED, HEIGHT_RELATIVE, GIMBAL_ANGLES,
  QUATERNION, dan RTK_POSITION_INFO. Nilai yang gagal dibaca tetap null.
- Bbox memakai AI recognition metadata terdaftar dan koordinat layar
  0–10.000.
- Contour piksel diraster ke AI-rendered RGB, lalu encoder callback mengirim
  H.264 melalui API payload camera resmi.
- Widget config menyediakan Start, Stop, detector switch, segmenter switch,
  Snapshot, status list, dan floating message FPS/latency.
- Shutdown menghentikan stream, callback, thread, subscription, file writer,
  dan core. Stall frame 5 detik memicu stop/start subscription yang terbatas.
- Identitas portal `ggp-drone-ai` / App ID `189927` divalidasi dari satu file
  lokal ignored. App key dan advanced license berstatus configured/redacted.
- Sample resmi yang dipakai adalah target C++
  `dji_sdk_demo_on_manifold3_cxx`. Header C dan C++ pada repo resmi tidak
  dimodifikasi; build menginjeksi header private generated dengan macro resmi
  `USER_APP_*`.
- App identity buffer divalidasi terhadap `T_DjiUserInfo`: app name 32, App ID
  16, key 32, license 512, developer account 64, dan baud rate 7 byte.

## Yang belum terverifikasi

Status: `psdk_blocked`.

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
API](https://developer.dji.com/doc/payload-sdk-tutorial/en/advanced-function/liveview.html),
[Liveview AR](https://developer.dji.com/doc/payload-sdk-tutorial/en/function-overview/advanced-function/liveview-ar.html),
dan [Custom
Widget](https://developer.dji.com/doc/payload-sdk-tutorial/en/basic-function/custom-widget.html).
