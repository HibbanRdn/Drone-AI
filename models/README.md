# Model artifacts

Folder ini adalah sumber tunggal artifact deployment. Setelah clone, jalankan:

~~~bash
git lfs install
git lfs pull
python3 scripts/model_preflight.py
~~~

Struktur:

- source: checkpoint PT resmi detector B0 manual-v1 dan segmenter B4 terpilih.
- onnx: export statis FP32, batch 1, opset 17, tanpa embedded NMS.
- engine: tujuan engine FP16 yang hanya boleh dibangun pada Manifold 3 atau
  environment identik.
- configs: provenance training, parameter export, tracking/unique count, ROI,
  dan gap analysis.
- model_manifest.json: checksum, ukuran, fungsi, shape, class, runtime target,
  dan status validasi.

Engine yang diharapkan:

- plant_center_manual_v1_b0_tensorrt-8.5.2_cuda-11.4_aarch64_fp16.engine
- plot_segmenter_b4_selected_tensorrt-8.5.2_cuda-11.4_aarch64_fp16.engine

Engine belum ada dan tidak dibuat di Mac/Windows. Setelah dibangun dan diuji di
Manifold, salin ke models/engine, perbarui manifest checksum/ukuran/status, lalu
commit dan push. Pola engine sudah dikonfigurasi untuk Git LFS.

Konfigurasi runtime utama berada di config/app.yaml dan semua path modelnya
relatif terhadap root repository. Loader mengubah path relatif tersebut menjadi
path absolut saat runtime berdasarkan lokasi config, bukan current working
directory.

Normalisasi tidak memakai mean/std khusus project. Runtime menyerahkan array BGR
OpenCV ke Ultralytics; backend melakukan konversi/preprocess dan skala 0..255 ke
0..1. Parameter ini dicatat eksplisit di config agar tidak diasumsikan berbeda.
