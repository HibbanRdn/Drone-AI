> Catatan handover: dokumen ini adalah dokumen historis sebelum konsolidasi v27. Untuk status canonical terbaru gunakan `README.md`, `HANDOVER.md`, dan dokumen handover utama di `docs/README.md`. Jangan menganggap versi/build/deploy di dokumen ini sebagai status final saat ini.

# Ground test checklist

Jangan jalankan motor atau flight. Propeller tidak diperlukan untuk test ini.

Prasyarat:

- inventory Manifold sudah ditinjau;
- preflight `--phase engine` dan `--phase runtime` lulus;
- tidak ada update firmware/OS/JetPack/CUDA/TensorRT;
- identitas aplikasi PSDK committed sudah ditinjau dan tidak dicetak ke log;
- official PSDK sample gate sudah lulus;
- ONNX dan FP16 engine checksum tercatat;
- parity engine lulus;
- aircraft dimatikan sebelum perubahan kabel E-Port.

Checklist:

- [ ] Manifold boot normal.
- [ ] `dji_app_ctl list/status` read-only berhasil.
- [ ] PSDK registration berhasil dan product type M4E terbaca.
- [ ] Mount type Manifold 3 onboard terbaca.
- [ ] Wide `M4E_VIS` RGB liveview diterima dengan frame ID/timestamp.
- [ ] Telemetry aktual masuk; field unsupported tetap null.
- [ ] Widget muncul di Pilot 2.
- [ ] Start/Stop AI bekerja tanpa reboot.
- [ ] Detector dan segmenter switch bekerja independen.
- [ ] Synthetic known boxes terpetakan tepat sebelum model digunakan.
- [ ] Bbox model tampil melalui AI metadata resmi.
- [ ] Contour plot tampil pada AI-rendered stream.
- [ ] Plant count diberi arti “frame saat ini”.
- [ ] Snapshot tersimpan hanya saat tombol ditekan.
- [ ] JSONL, telemetry, metrics, session summary, dan log valid.
- [ ] Frame corrupt/empty tidak mematikan proses.
- [ ] Stream reconnect diuji tanpa backlog.
- [ ] Stop menutup stream, encoder, worker, file, dan resource TensorRT.
- [ ] Start ulang berhasil tanpa reboot.
- [ ] Memory, temperature, power mode, latency, dan FPS dicatat minimal 15 menit.
- [ ] Kedua model bersama tidak memicu thermal/memory fault.
- [ ] DPK baru dipertimbangkan setelah seluruh item di atas lulus.

Catat command, timestamp, versi aktual, dan hasil ringkas; jangan salin serial
number atau credential ke report.
