# Offline deployment dari Windows

Final package hanya dibuat setelah diff disetujui dan commit final tersedia.
Generator menolak worktree kotor, branch salah, PSDK tidak resmi, credential
yang dilacak Git, wheel target salah, engine/media/runtime output, dan secret
umum.

## Persiapan satu kali

```powershell
$Repo = "<Drone-AI>"
$Parent = Split-Path -Parent $Repo
$PsdkRoot = Join-Path $Parent "Payload-SDK-3.16.0"
$OfflineWheels = Join-Path $Parent "Gap-Plot-Offline-Assets\offline_wheels"
$OutputDir = Join-Path $Parent "Gap-Plot-Deploy-Packages-LF"

git -C $Repo switch feature/pilot-liveview-inference
git -C $Repo pull --ff-only
git clone --branch 3.16.0 https://github.com/dji-sdk/Payload-SDK.git $PsdkRoot

New-Item -ItemType Directory -Force $OfflineWheels
py -m pip download --only-binary=:all: --no-deps `
  --platform manylinux2014_aarch64 --python-version 38 `
  --implementation cp --abi cp38 PyYAML==6.0.2 `
  --dest $OfflineWheels
```

Credential nyata tidak dilacak Git. Salin file yang sudah disediakan secara
aman ke `<Drone-AI>\config\dji_sdk_app_info.local.h`, yaitu folder `config`
di root repository yang sama dengan source hasil `git pull`; jangan mengedit
nilainya dan jangan mengirimnya lewat chat/email. Semua script default membaca
credential dari path relatif root repo tersebut. Validator hanya mencetak App ID
dan status redacted. Jika final package dibuat di Mac dan dipindahkan ke
Windows, gunakan mode `-PackagePath` pada script sehingga Windows tidak
memerlukan header.

```powershell
powershell -ExecutionPolicy Bypass -File "$Repo\scripts\deploy_manifold_windows.ps1" `
  -PackagePath "$OutputDir\EXACT_PACKAGE.tar.gz" `
  -SshHost gap-plot-manifold
```

## Build package baru dan transfer

```powershell
powershell -ExecutionPolicy Bypass -File "$Repo\scripts\deploy_manifold_windows.ps1" `
  -PsdkRoot $PsdkRoot `
  -OfflineWheels $OfflineWheels `
  -SshHost gap-plot-manifold
```

Script membuat nama unik, menolak paket legacy
`gap_plot_ai_offline_21f56625f908_20260803T090552Z`, memverifikasi sidecar dan
isi tar sebelum transfer, lalu menyalin paket ke
`/home/dji/gap_plot_ai_transfer`. Arsip mengandung credential; batasi ACL
folder output ke akun operator, jangan sinkronkan ke cloud, dan hapus secara
aman hanya setelah deployment/backup policy disetujui.

## Isi package v2

```text
gap_plot_ai_offline_<commit>_<UTC>/
  manifest.json
  SHA256SUMS
  sources/Drone-AI-source.tar
  bundles/Payload-SDK-3.16.0.bundle
  credentials/dji_sdk_app_info.local.h
  offline_wheels/PyYAML-6.0.2-...-aarch64.whl
  install/
    verify_offline_archive.py
    verify_package.py
    verify_offline.sh
    install_offline.sh
    build_manifold_offline.sh
    README_TRANSFER.md
```

Source snapshot tidak membawa `.git` atau credential. PSDK bundle membawa
exact official tag. Manifest dan `SHA256SUMS` mencatat byte final setelah
normalisasi: Bash/Python/config LF tanpa BOM, PowerShell CRLF, Bash executable.
Verifier menolak traversal, absolute path, link/device, CRLF Bash, mode salah,
virtualenv, checksum tidak lengkap, dan wheel non-aarch64/non-Python-3.8.

Rollback hanya mengganti symlink `current` ke nilai
`previous_current.txt`; installer tidak menghapus source legacy atau release
baru.
