# Troubleshooting Manifold

| Gejala | Pemeriksaan read-only | Tindakan aman |
|---|---|---|
| `PSDK_ROOT` ditolak | `python3 scripts/verify_psdk_316.py "$PSDK_ROOT"` | gunakan bundle package exact tag; jangan patch vendor |
| library PSDK hilang | periksa `psdk_lib/lib/aarch64-linux-gnu-gcc/libpayloadsdk.a` | hentikan build; ulang transfer/package verification |
| wheel ditolak | lihat nama di `offline_wheels/` | download ulang CPython 3.8 Linux aarch64; jangan pakai wheel Windows/macOS/x86 |
| `yaml` hilang | `.venv/bin/python -c 'import yaml'` | ulang `build_manifold_offline.sh`; tidak boleh akses internet |
| OpenCV path/version salah | `check_manifold_ai_runtime.py --phase runtime` | hentikan; jangan menimpa OpenCV image perangkat |
| TensorRT deserialize gagal | `validate_engine_readonly.sh` | cocokkan TRT/CUDA/engine hash; jangan rebuild/refit |
| binding mismatch | lihat `tensorrt_engine.json` | sinkronkan kontrak config dengan engine tervalidasi setelah review; jangan menebak decoder |
| CUDA OOM | `tegrastats`, log worker | Stop AI, tunggu cleanup; jangan ubah batch (harus 1) |
| `StartImageStream` gagal | code hex native log | cek firmware/product/mount/sample resmi; fallback H.264 tetap off |
| stream putus | state/error, frame timestamp | Stop→Start setelah stream pulih; jangan reboot kecuali DJI procedure memerlukan |
| overlay tidak terlihat | static five-box test dan return code | cek label registration/metadata capability/Pilot firmware |
| overlay terpotong | `total_detections`, `sent_objects` | expected jika > configured/ABI limit; JSONL tetap lengkap |
| AI tetap RUNNING tanpa FPS | heartbeat/status worker | Stop; worker death harus menghasilkan ERROR, bukan status palsu |
| disk rendah | `df -h /home/dji` dan session summary | hentikan logging; pindahkan session secara terkontrol, jangan hapus legacy |
| native library `not found` | `ldd build/bin/gap_plot_ai` | hentikan; jangan menambah library acak atau `sudo` |
| CRLF/`^M` | jalankan verifier package | buat ulang paket; jangan edit shell script di Manifold |

Kumpulkan `runtime/diagnostics`, `runtime/reports`, native/worker log, session
summary, hash engine, firmware dan return code. Jangan sertakan header
credential, raw frames, serial number, lokasi sensitif, atau engine pada issue.
