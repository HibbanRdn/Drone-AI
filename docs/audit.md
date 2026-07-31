# Audit lokal

Tanggal audit: 31 Juli 2026.

## Project dan Git

- Root: `/Users/muhamadhibbanramadhan/Documents/Plot Gap`.
- Demo aktif: `local_inference/b0_manual_v1_video_demo`.
- Project berada di dalam working tree parent yang sudah berisi banyak data
  user; tidak ada commit, push, reset, atau penghapusan dilakukan.
- Pipeline/demo lama dan weight sumber tidak dimodifikasi.
- `.gitignore` root ditambah untuk venv, build, runtime, session, log, JSONL,
  model/weight, ONNX, TensorRT engine, video, DPK intermediate, dan secret.

## Payload SDK

Sumber kebenaran adalah upstream DJI exact tag `3.16.0`, commit
`9af08536df3671ed77aee02f1f2e4173b0e0f558`. Library resmi aarch64
`libpayloadsdk.a` memiliki SHA-256
`c940d6d88f449ef6f48e535e0b765f4bf8486db44702d4d64e37da156d204426`.

Mirror/reference `/Users/muhamadhibbanramadhan/Documents/Payload-SDK-master`
berada pada commit berbeda
`9bbacae3e3a5fd9e8a3d550fc3692cd212490341`, sehingga tidak lagi diterima
oleh script build sebagai bukti exact tag walaupun library aarch64 dan file
Manifold 3 relevannya identik. Repo mirror sudah mempunyai perubahan user:
`samples/sample_c++/module_sample/liveview/data/tensorflow/frozen_inference_graph.pb`
berstatus deleted. Perubahan tersebut dipertahankan.
- Direktori bernama `Payload-SDK-master_LOCAL_SECRETS_DO_NOT_COMMIT` ditemukan
  tetapi tidak dibuka atau dibaca.

Sample upstream 3.16.0 yang dipakai sebagai sumber kontrak adalah Manifold 3,
liveview object detection, custom widget, FC subscription, dan build DPK resmi.
Source header mengonfirmasi:

- wide M4E: `DJI_LIVEVIEW_CAMERA_SOURCE_M4E_VIS`;
- decoded frame Manifold 3: `PIXFMT_RGB_PACKED`;
- bbox Pilot 2: `DjiLiveview_RegUserAiTargetLableList` dan
  `DjiLiveview_SendAiMetaToPilot`;
- AI stream: `DjiLiveview_EncodeAFrameToH264` lalu
  `DjiPayloadCamera_SendVideoStream`;
- Open AR contour memakai longitude/latitude/altitude, sehingga tidak dipakai
  untuk contour piksel tanpa georeferencing.

README release 3.16.0 menyebut dukungan dependency melalui `install.py`, tetapi
`git ls-tree` pada exact tag tidak memuat `install.py` maupun `install.sh`.
Project ini tidak memanggil atau mengarang script instalasi pengganti. Kode
adapter Wi-Fi RTL8852BU/RTL88X2BU juga tidak dimasukkan karena aplikasi tidak
mengelola konfigurasi jaringan.

## Model registry dan pipeline lama

| Model | Task/class | Konfigurasi sumber | SHA-256 sumber |
|---|---|---|---|
| `plant_detector_b0_manual_v1_best.pt` | detect, `0=plant` | 1024; conf 0,2; NMS 0,1; tile 1024/overlap 128; global NMS 0,1; center suppression 8 px; maks 1.000/tile dan 5.000/frame | `5076ac21814b4ad1955dbdc3cc7aa25dca62659914467e0e651fbb7f8d821266` |
| `plot_segmenter_b4_selected_best.pt` | segment, `0=plantable_area` | 1280; selected conf 0,03; NMS 0,7; opening lalu closing kernel 3 | `1fd2a8d78fda17f3f2938035aced92f0c2aa346cdf26df3d71b87a15982f3519` |

Kedua descriptive path adalah symlink. Target dan registry cocok dengan
weight sumber di `Downloads`; SHA-256 symlink dan target identik. Tidak ada
weight yang dipindahkan, ditimpa, atau dihapus.

Detector lama mengembalikan bbox pixel global setelah tiled inference,
class-aware global NMS, dan center-distance suppression. Runtime baru memakai
algoritme dan nilai yang sama. Segmenter membentuk union mask, resize ke ukuran
frame, morphology, lalu contour sederhana.

Sebelum pekerjaan ini tidak ditemukan ONNX atau TensorRT engine untuk kedua
model. Hasil export saat ini dicatat di `model_validation.md`.

## Dependency lokal terverifikasi

- Python 3.10.19
- PyTorch 2.7.1
- Torchvision 0.22.1
- Ultralytics 8.4.96
- NumPy 2.2.6
- OpenCV 4.11.0
- ONNX 1.22.0
- ONNX Runtime 1.23.2

Venv terisolasi ada di `manifold_app/.venv`. Percobaan awal yang tidak cocok
dengan Python 3.14 dipindahkan secara recoverable ke path ignored
`.venv_py314_failed`; tidak dipakai runtime.

## Video uji

Path yang disebut tanpa subfolder tidak ada. Video yang benar ditemukan secara
terarah di:

`/Users/muhamadhibbanramadhan/Movies/New Flight/recut_with_srt/pipeline_input_part1/DJI_20260729141517_0030_V_part1_000-037.MP4`

HEVC, 3840×2160, 30000/1001 fps, 1.109 frame, durasi 37,003633 detik.

## Hasil audit

Source dan lokal: `local_ready`, `model_export_ready`.

Belum dibuktikan pada hardware: `hardware_blocked`, `psdk_blocked`;
`on_device_validated=false`; `dpk_ready=false`.

## Identitas DJI Developer

- App Name: `ggp-drone-ai`.
- App ID: `189927`.
- Apply status: `accepted`.
- Application status: `Not Verified`.
- Batas distribusi sebelum verification: maksimal 20 perangkat.
- Service/package internal tetap `gap_plot_ai`.
- DPK identifier dipetakan ke `ggp-drone-ai`.
- Alias yang akan tampil di Pilot 2: `Gap Plot AI`.
- Tidak ada unit systemd custom; development memakai runner foreground,
  sedangkan DPK nantinya dikelola oleh `dji_app_ctl`.

Credential sensitif telah dikonfigurasi pada file lokal ignored berizin `600`
dan tidak disalin ke dokumen, source header, atau DPK staging. Validator
redacted lulus untuk App ID, key, dan advanced license. PSDK 3.16 masih
memerlukan `developerAccount[64]`; email akun developer tidak terdapat pada
screenshot dan belum diisi.
