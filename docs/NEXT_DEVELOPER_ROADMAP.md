# Roadmap Developer Berikutnya

Roadmap ini memprioritaskan preservation, correctness, build provenance, dan hardware validation. Optimasi dilakukan setelah baseline benar.

## P0 - Baca Handover Dan Preserve State

Tujuan:

Developer memahami status project sebelum menyentuh build/deploy.

Langkah:

- baca `README.md`;
- baca `HANDOVER.md`;
- baca `docs/SOURCE_PROVENANCE.md`;
- pahami perbedaan v27 source dan v26 runtime.

Exit criteria:

```text
developer dapat menjelaskan:
latest development = v27
latest built/deployed runtime = v26
v27 belum build/package/deploy
```

## P1 - Reconcile Build/Version Metadata

Tujuan:

Menghilangkan ambiguity sebelum membuat artifact v27.

Langkah:

- pilih version v27 yang benar;
- review `debugVersion` di `src/psdk/main.cpp`;
- review `scripts/build_dpk.sh`;
- review `dpk/app.json.in`;
- putuskan launcher vs native worker spawn;
- dokumentasikan keputusan final.

Exit criteria:

```text
version, DPK filename, app.json, native metadata, dan startup path konsisten
```

## P2 - Review Canonical v27 Source

Tujuan:

Memastikan source canonical siap divalidasi tanpa mengulang investigasi snapshot.

Langkah:

- review `src/gap_plot_ai/finalizer.py`;
- review `src/gap_plot_ai/runtime.py`;
- review `src/gap_plot_ai/worker.py`;
- review `src/gap_plot_ai/tensorrt_backend.py`;
- review `src/psdk/main.cpp`;
- review config di `config/app.yaml` dan `config/live.yaml`;
- review issue `RegistrationFrameWriter`.

Exit criteria:

```text
tidak ada source path ganda yang membingungkan
known issue dipahami sebelum build
```

## P3 - Build v27

Tujuan:

Membuktikan source v27 compile pada target.

Langkah:

- gunakan Manifold 3 atau Linux aarch64 target-identical;
- pastikan PSDK 3.16.0 resmi;
- pastikan credential lokal tersedia tanpa masuk Git;
- build native dengan CMake;
- catat binary hash dan build log.

Exit criteria:

```text
native binary v27 berhasil dibuat dan provenance tercatat
```

## P4 - Package v27 Menjadi DPK

Tujuan:

Membuat DPK v27 setelah version/build jelas.

Langkah:

- stage native binary;
- stage Python payload;
- stage `config/app.yaml`, `config/live.yaml`, dan `config/widget/`;
- pastikan `.engine` tidak masuk DPK;
- generate dan validate `app.json`;
- jalankan builder resmi PSDK;
- catat DPK hash.

Exit criteria:

```text
DPK v27 ada, hash tercatat, dan tidak ambigu dengan v26
```

## P5 - Deploy Secara Terkontrol

Tujuan:

Membuktikan installed runtime benar-benar berasal dari DPK v27.

Langkah:

- install melalui workflow DJI yang disetujui;
- readback installed version;
- readback native binary hash;
- readback payload Python/config;
- verifikasi widget assets.

Exit criteria:

```text
installed runtime = v27 dan cocok dengan artifact provenance
```

## P6 - Runtime Validation Di Manifold

Tujuan:

Membuktikan runtime loop berjalan pada hardware.

Langkah:

- start app dengan `dji_app_ctl`;
- verifikasi PSDK init;
- verifikasi Matrice 4E dan Extension Port;
- verifikasi widget registration;
- verifikasi decoded RGB liveview active;
- verifikasi worker hanya start satu kali;
- verifikasi `latest_frame.rgb`, `latest_result.txt`, dan `worker_status.json`;
- verifikasi detector/segmenter engine load;
- stop AI dan pastikan finalizer selesai.

Exit criteria:

```text
start -> liveview -> inference -> stop -> finalizer terbukti pada runtime v27
```

## P7 - Pilot Overlay Validation

Tujuan:

Membuktikan bounding box yang dikirim native terlihat benar di DJI Pilot.

Langkah:

- validasi `BOX` result dari worker;
- validasi conversion ke koordinat DJI 0..10000;
- validasi `DjiLiveview_SendAiMetaToPilot()`;
- validasi alignment visual di Pilot;
- validasi stale clearing dan cap box count.

Exit criteria:

```text
Pilot overlay stabil dan aligned pada real liveview
```

## P8 - Field / RTK / Geospatial / Gap Validation

Tujuan:

Membuktikan output AI berguna secara agronomis dan geospatial.

Langkah:

- gunakan field truth;
- validasi plant detection precision/recall;
- validasi gap count dan coordinate;
- validasi false positive pada row-end/headland/road;
- validasi RTK/geospatial export;
- pisahkan local-pixel output dari true geographic output.

Exit criteria:

```text
gap result dan export dapat dipercaya untuk skenario field yang didefinisikan
```

## P9 - Performance Optimization

Tujuan:

Optimasi setelah correctness terbukti.

Langkah:

- ukur FPS, latency, frame drops, CPU/GPU/memory, thermal;
- review tiling;
- review segmenter interval/cache;
- review max overlay boxes;
- lakukan long-duration soak test.

Exit criteria:

```text
performance membaik tanpa mengubah correctness yang sudah divalidasi
```
