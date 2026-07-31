#!/usr/bin/env bash
set -euo pipefail

cat >&2 <<'EOF'
DPK build sengaja diblokir pada tahap ini.

Alasan terverifikasi:
- development runtime masih bergantung pada worker Python/Ultralytics;
- inventory target belum memiliki ONNX, PyTorch, atau Ultralytics;
- dokumentasi Manifold Application DJI mewajibkan dependency library aplikasi
  statis dan path runtime relatif;
- template app.json masih memiliki ver_min/ver_max yang belum diketahui dari
  firmware package Manifold dan belum memiliki staging package final;
- official PSDK sample, liveview, TensorRT engine, parity, dan ground test belum
  lulus pada perangkat ini.
- config/secrets.env dan generated credential header dilarang masuk staging;
  identitas aplikasi hanya boleh berada pada binary yang sudah dikompilasi.

Jangan membuat atau menginstal DPK semu. Lanjutkan setelah on-device validation
dan runtime inference native/static atau mekanisme dependency yang didukung DJI
telah dibuktikan pada firmware Manifold aktual.
EOF
exit 4
