> Catatan handover: dokumen ini adalah dokumen historis sebelum konsolidasi v27. Untuk status canonical terbaru gunakan `README.md`, `HANDOVER.md`, dan dokumen handover utama di `docs/README.md`. Jangan menganggap versi/build/deploy di dokumen ini sebagai status final saat ini.

# Manifold 3 runtime matrix

Target dikunci ke kombinasi berikut sampai ground test menyatakan sebaliknya.
Versi perangkat berasal dari baseline operator dan release note resmi; host
macOS hanya memverifikasi source/config, bukan binary atau hardware.

| Komponen | Target | Status saat ini |
|---|---:|---|
| Manifold 3 firmware | `17.00.01.01` | dilaporkan; perlu inventori device |
| Matrice 4 Series firmware | `17.00.00.01` | dilaporkan; perlu hardware test |
| DJI RC Plus 2 firmware | `01.64.08.06` | dilaporkan; perlu hardware test |
| DJI Pilot 2 | `17.0.0.40` | dilaporkan; perlu hardware test |
| Payload SDK | `3.16.0` / `9af08536df3671ed77aee02f1f2e4173b0e0f558` | source resmi lokal terverifikasi |
| OS | Ubuntu 20.04 / Linux aarch64 | perlu inventori device |
| L4T / JetPack | R35.5 / JetPack 5.1.3 | target resmi NVIDIA |
| Python | `3.8.10` | perlu inventori device |
| CUDA | `11.4` | perlu inventori device |
| TensorRT | `8.5.2.2` | perlu inventori device |
| OpenCV | `4.5.4` | perlu inventori device |
| TensorRT engine | existing protected engine | perlu inspeksi read-only |

Source PSDK lokal:

```text
../Payload-SDK-3.16.0
origin: https://github.com/dji-sdk/Payload-SDK.git
tag: 3.16.0
commit: 9af08536df3671ed77aee02f1f2e4173b0e0f558
submodules: none
Git LFS: none
```

Library target berada di
`psdk_lib/lib/aarch64-linux-gnu-gcc/libpayloadsdk.a`; sample resmi target
adalah `samples/sample_c++/platform/linux/manifold3` dan
`dji_sdk_demo_on_manifold3_cxx`.

Runtime live sengaja tidak membutuhkan Torch, TorchVision, Ultralytics, ONNX,
atau ONNX Runtime. Komponen tersebut tetap host-only untuk export/parity.
Runtime device memakai Python system packages untuk NumPy/OpenCV/TensorRT dan
wheel offline `PyYAML==6.0.2` untuk CPython 3.8 Linux aarch64.

README checkout PSDK 3.16.0 menyebut instalasi dependency rootless pada
Manifold 3 melalui script `install.py`; checkout tag lokal tidak memuat
`install.py` ataupun `install.sh`. Karena itu source tidak mengasumsikan
installer vendor tersedia. Inventori device dan build sample resmi adalah
sumber kebenaran berikutnya, tanpa `sudo` atau internet.

Referensi resmi:

- https://github.com/dji-sdk/Payload-SDK
- https://enterprise.dji.com/manifold-3/downloads
- https://dl.djicdn.com/downloads/Manifold_3/RN/20260331/Manifold_3_Release_Notes_en.pdf
- https://developer.nvidia.com/embedded/jetpack-sdk-513
