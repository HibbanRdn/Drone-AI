# Hardware acceptance — M4E / Manifold 3 / Pilot 2

Semua item sengaja belum dicentang. Replay/fake backend tidak boleh dipakai
untuk memberi status lulus perangkat.

## Baseline dan source

- [ ] PSDK exact `3.16.0` / commit `9af08536…`.
- [ ] Firmware aircraft, Pilot 2/RC, camera, dan Manifold dicatat.
- [ ] CUDA, TensorRT, Torch, TorchVision, OpenCV, engine checksum dicatat.
- [ ] Device `git rev-parse HEAD` dan `git diff --stat` dicatat tanpa secret.
- [ ] Config efektif: batch 1, tile 1024, overlap 128, threshold existing,
      TorchVision CUDA NMS, center suppression false.
- [ ] Tidak ada credential, serial number, raw media, engine besar, atau
      runtime session ditambahkan ke Git.

## Official sample gate

- [ ] Aircraft di tanah, motor off.
- [ ] Official Manifold 3 C++ sample build/run berhasil.
- [ ] Product type M4E dan mount Manifold 3 onboard terbaca.
- [ ] M4E_VIS decoded callback diterima.
- [ ] Resolution, `PIXFMT_RGB_PACKED`, row stride, frame ID, UTC, dan
      monotonic timestamp dicatat.
- [ ] Widget official sample tampil di Pilot 2.
- [ ] FC topics tersedia; unsupported fields tetap null.
- [ ] Marker PSDK dibuat hanya sesudah bukti tersebut.

## Static overlay sebelum model

```bash
./scripts/run_static_overlay_test.sh
```

- [ ] Kotak center tepat.
- [ ] Kotak kiri atas tepat.
- [ ] Kotak kanan atas tepat.
- [ ] Kotak kiri bawah tepat.
- [ ] Kotak kanan bawah tepat.
- [ ] Aspect ratio, crop, dan orientation Pilot dicatat.
- [ ] Metadata muncul pada normal DJI liveview.
- [ ] API rejection menghasilkan code PSDK dan ERROR jelas.
- [ ] Normal camera recording Start/Stop tetap berfungsi.

## Live plant detector

```bash
./scripts/run_live_dev.sh
```

- [ ] App/alias `Gap Plot AI DEV` muncul.
- [ ] Initial IDLE; Start melewati STARTING/WARMING UP ke RUNNING.
- [ ] `model_load_count=1`, `backend_initialization_count=1`, dan
      `warmup_count=1`.
- [ ] Log TensorRT membuktikan satu detector/context yang diperlukan.
- [ ] Native liveview resolution diproses tanpa upscaling full-frame ke 4K.
- [ ] Source FPS, AI FPS, last/p50 latency, plant total, Pilot count tampil.
- [ ] Gap tetap N/A; plant tidak disebut final gap.
- [ ] Semua detection JSONL, termasuk overlay-truncated, tersimpan.
- [ ] Overlay tidak melebihi 255; total/sent/truncated tercatat.
- [ ] Result lebih tua dari 1500 ms tidak terus tampil.
- [ ] Telemetry terkait frame sequence/monotonic timestamp yang sama.
- [ ] Queue tidak tumbuh; dropped counter boleh naik saat AI sibuk.

## Stop, error, dan recovery

- [ ] Stop: STOPPING lalu IDLE; Stop kedua idempotent.
- [ ] Start kedua RUNNING tanpa reload engine (`model_load_count` dan
      `backend_initialization_count` tetap 1).
- [ ] Stop saat inference aktif menunggu current frame dan flush session.
- [ ] Putus stream menghasilkan STREAM_TIMEOUT/ERROR, bukan RUNNING palsu.
- [ ] Pulihkan stream, Stop→Start berhasil tanpa reboot.
- [ ] Engine path salah menghasilkan START_FAILED/engine missing.
- [ ] CUDA/TensorRT/OOM menghasilkan ERROR dan `errors.log`.
- [ ] Worker kill menghasilkan heartbeat error di Pilot.
- [ ] Low disk/storage cap error terlihat.
- [ ] Callback setelah shutdown tidak menulis frame atau crash.

## Ground endurance 10–15 menit

- [ ] Pilot 2 dan normal liveview responsif.
- [ ] Normal camera recording tetap berjalan.
- [ ] AI FPS sesuai kemampuan benchmark tanpa klaim target palsu.
- [ ] RAM/GPU memory tidak terus naik.
- [ ] Temperature, power mode, CPU/GPU, disk dicatat tiap menit.
- [ ] Tidak ada queue growth, worker/context ganda, atau corrupt session.
- [ ] Stop final menghasilkan JSONL dan summary valid.
- [ ] App dapat dijalankan kembali tanpa reboot.

## Short flight

- [ ] Flight disetujui pilot/observer; app tetap read-only.
- [ ] GPS/RTK status/quality tersimpan.
- [ ] Aircraft/gimbal attitude, velocity, altitude, camera source tersimpan.
- [ ] Tidak memengaruhi flight, gimbal, waypoint, RTH, atau recording.
- [ ] Session AI dan recording normal lengkap.
- [ ] Session ditutup normal setelah landing/Stop.
- [ ] Tidak ada SHP/gap geolocation yang diklaim.

## DPK dan rollback

- [ ] `ver_min/ver_max` berasal dari firmware package Manifold.
- [ ] Target runtime bundle dan dependency packaging resmi lulus.
- [ ] `scripts/package_dpk.sh` menghasilkan DPK + SHA-256.
- [ ] Secret scan staging lulus.
- [ ] DPK dapat dipilih dan diinstal melalui application management Pilot 2.
- [ ] DPK terinstal dan `dji_app_ctl status ggp-drone-ai` sehat.
- [ ] Ikon/widget tampil setelah cold boot.
- [ ] Rollback last-known-good diuji tanpa menyentuh aplikasi DJI lain.
