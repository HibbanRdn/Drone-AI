# Audit lokal PSDK 3.16.0 — macOS arm64

## Baseline resmi

- Repository: `https://github.com/dji-sdk/Payload-SDK.git`
- Tag: `3.16.0`
- Commit: `9af08536df3671ed77aee02f1f2e4173b0e0f558`
- Tag type: lightweight tag ke commit di atas
- Submodule: tidak ada
- Git LFS: tidak ada
- Library target:
  `psdk_lib/lib/aarch64-linux-gnu-gcc/libpayloadsdk.a`
- SHA-256 library:
  `c940d6d88f449ef6f48e535e0b765f4bf8486db44702d4d64e37da156d204426`
- Sample target: C/C++ Linux Manifold 3, termasuk USB bulk HAL, H.264
  Liveview decoder, decoded image/object detection, metadata AI, dan DPK tool.

## Verified on macOS

- origin, tag, commit, header version, vendor cleanliness, absence of
  submodule/LFS, aarch64 library checksum, and required sample files;
- exact Liveview enum/API tokens and camera source `M4E_VIS`;
- C++ syntax against official 3.16.0 headers (without aarch64 linking);
- Python tests, replay fake backend, Bash syntax, config validation, CMake
  dependency mapping, and offline package guard/templates.

## Requires Manifold build

- CMake configure/link against aarch64 `libpayloadsdk.a`;
- official Manifold 3 sample build with the committed application identity;
- Python 3.8/TensorRT/CUDA/TorchVision target imports and existing engine;
- DPK tool execution and firmware range validation.

## Requires aircraft/live hardware test

- M4E source/position acceptance and decoded RGB format/stride/resolution;
- H.264 fallback need, FFmpeg 4 decoder build if required, I-frame/reconnect;
- AI metadata and static five-box alignment in Pilot 2;
- optional rendered payload-camera stream;
- Start/Stop/restart, telemetry, normal recording coexistence, and soak/flight.

Tidak ada akses Manifold atau deployment perangkat pada audit lokal ini.
