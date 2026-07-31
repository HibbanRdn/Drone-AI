# Inventory read-only Manifold 3 dari Windows

Panduan ini ditujukan untuk laptop Windows 10/11. Tujuannya hanya memeriksa
konektivitas dan mengumpulkan inventory DJI Manifold 3 melalui SSH. Prosedur
ini tidak melakukan build, deploy, update firmware, flight control, atau
perubahan konfigurasi aircraft maupun Manifold.

Gunakan PowerShell untuk pemeriksaan aplikasi dan jaringan. Gunakan Git Bash
untuk mengirim `scripts/manifold_inventory_readonly.sh` melalui SSH. OpenSSH
bawaan Windows atau OpenSSH dari Git for Windows dapat digunakan.

## A. Pemeriksaan aplikasi di PowerShell

Buka PowerShell, lalu periksa aplikasi yang dibutuhkan:

```powershell
git --version
gh --version
ssh -V
```

Jika `git` belum tersedia, instal Git for Windows terlebih dahulu. Jika `gh`
belum tersedia, instal GitHub CLI terlebih dahulu. Lakukan instalasi secara
manual dari sumber resminya; panduan ini tidak menjalankan instalasi otomatis.

## B. Login GitHub

Jalankan:

```powershell
gh auth login
gh auth status
```

Saat diminta, pilih:

```text
GitHub.com
HTTPS
Login with a web browser
```

Selesaikan login di browser. Jangan menulis token, password, atau credential
GitHub ke dokumentasi maupun file repository.

## C. Clone repository private

```powershell
cd "$HOME\Documents"
gh repo clone HibbanRdn/Drone-AI
cd ".\Drone-AI"
git status -sb
```

Pastikan script inventory tersedia:

```powershell
Test-Path ".\scripts\manifold_inventory_readonly.sh"
```

Hasil yang diharapkan:

```text
True
```

## D. Persiapan koneksi Manifold

Ikuti urutan perangkat ini:

1. Matikan aircraft.
2. Pastikan Manifold 3 terpasang dan terhubung ke aircraft.
3. Sambungkan laptop Windows ke debug/configuration USB-C port aircraft
   menggunakan kabel data.
4. Hidupkan aircraft.
5. Tunggu sekitar 3–5 menit hingga Manifold selesai boot.
6. Jangan melakukan hot-plug saat pengujian.

## E. Pemeriksaan jaringan dari PowerShell

Tampilkan konfigurasi jaringan:

```powershell
ipconfig
```

Cari adapter USB/Ethernet DJI dengan alamat pada jaringan `192.168.42.x`.
Kemudian jalankan:

```powershell
ping 192.168.42.120
Test-NetConnection 192.168.42.120 -Port 22
```

Hasil berikut berarti layanan SSH dapat dijangkau:

```text
TcpTestSucceeded : True
```

Jika nilainya `False`, berhenti dan lakukan troubleshooting jaringan. Jangan
menjalankan script inventory sebelum port SSH dapat dijangkau.

## F. Tes masuk SSH

Dari PowerShell:

```powershell
ssh -o ConnectTimeout=30 dji@192.168.42.120
```

Setelah berhasil masuk, jalankan pemeriksaan sederhana berikut:

```bash
hostname
whoami
exit
```

Jangan menulis atau menyimpan password Manifold di repository, command history,
script, atau laporan audit.

## G. Menjalankan inventory dari Git Bash

Buka Git Bash, lalu masuk ke repository dan siapkan direktori laporan lokal:

```bash
cd "/c/Users/$USERNAME/Documents/Drone-AI"
mkdir -p runtime/reports
```

Validasi syntax script:

```bash
bash -n scripts/manifold_inventory_readonly.sh
```

Jalankan inventory:

```bash
ssh -o ConnectTimeout=30 dji@192.168.42.120 \
  'bash -s' < scripts/manifold_inventory_readonly.sh \
  | tee runtime/reports/manifold_inventory_raw.txt
```

Script tidak perlu disalin ke Manifold. Isi script dikirim melalui SSH dan
dijalankan oleh `bash` pada Manifold. `tee` menampilkan output di terminal
sekaligus menyimpannya di:

```text
runtime/reports/manifold_inventory_raw.txt
```

Direktori `runtime/` diabaikan Git. Laporan perangkat tidak boleh ditambahkan
ke commit atau dipush ke GitHub.

## H. Membuka hasil audit

Dari Git Bash:

```bash
sed -n '1,200p' runtime/reports/manifold_inventory_raw.txt
```

Atau dari PowerShell, saat berada di root repository:

```powershell
Get-Content ".\runtime\reports\manifold_inventory_raw.txt"
```

Tinjau hasil sebelum membagikannya. Jangan sertakan serial number, hostname
yang tidak diperlukan, password, App Key, Advanced License, token, private
key, atau credential lain.

## Troubleshooting

### `Test-Path` menghasilkan `False`

Pastikan PowerShell berada di root repository `Drone-AI`, lalu jalankan:

```powershell
git status -sb
git pull --ff-only origin main
git ls-files scripts/manifold_inventory_readonly.sh
```

Jika file tetap tidak ditemukan, hapus hanya clone yang tidak lengkap setelah
memastikan tidak ada pekerjaan lokal, lalu clone ulang. Jangan membuat script
pengganti secara manual.

### Adapter `192.168.42.x` tidak muncul

Hentikan audit. Pastikan aircraft dimatikan sebelum memeriksa pemasangan
Manifold dan kabel, gunakan kabel USB-C yang mendukung data, pastikan port
debug/configuration aircraft yang benar digunakan, lalu ulangi urutan power-on.
Periksa juga Windows Device Manager untuk adapter USB/Ethernet yang gagal
terdeteksi. Jangan hot-plug dan jangan mengubah konfigurasi jaringan aircraft.

### `Destination host unreachable`

Laptop belum memiliki rute yang benar menuju `192.168.42.120`. Periksa kembali
adapter `192.168.42.x` dengan `ipconfig`. Hentikan VPN atau jaringan lain yang
memiliki rute konflik hanya jika diizinkan oleh kebijakan laptop, kemudian
ulangi pemeriksaan. Jangan menebak alamat statis atau mengubah konfigurasi
aircraft/Manifold.

### `TcpTestSucceeded : False`

Jangan jalankan inventory. Pastikan Manifold telah boot selama 3–5 menit,
alamat adapter benar, dan ping/rute tersedia. Ulangi:

```powershell
Test-NetConnection 192.168.42.120 -Port 22
```

Jika tetap `False`, catat hasil dan lanjutkan troubleshooting fisik/jaringan,
bukan deployment atau perubahan service perangkat.

### `Connection timed out`

Alamat tidak dapat dijangkau atau layanan SSH tidak merespons. Periksa adapter,
rute, kabel data, urutan boot, dan hasil `Test-NetConnection`. Jangan menambah
opsi untuk melewati verifikasi keamanan SSH.

### `Connection refused`

Host dapat dijangkau tetapi port SSH tidak menerima koneksi. Tunggu Manifold
selesai boot dan ulangi pemeriksaan port. Jika tetap ditolak, hentikan audit
dan laporkan; jangan mengaktifkan atau mengubah service SSH perangkat.

### `Permission denied`

Pastikan username adalah `dji` dan gunakan metode autentikasi yang memang
diberikan untuk perangkat tersebut. Periksa keyboard layout jika memasukkan
password secara interaktif. Jangan menyimpan password atau private key di
repository.

### Peringatan SSH host key berubah

Jangan abaikan peringatan dan jangan memakai `StrictHostKeyChecking=no`.
Verifikasi terlebih dahulu bahwa laptop benar-benar terhubung ke aircraft dan
Manifold fisik yang dimaksud. Periksa entry khusus:

```powershell
ssh-keygen -F 192.168.42.120
```

Hanya setelah perubahan perangkat diverifikasi secara fisik, hapus entry untuk
alamat tersebut saja:

```powershell
ssh-keygen -R 192.168.42.120
```

Jangan menghapus seluruh file SSH `known_hosts`.

### `$'\r': command not found`

File shell ter-checkout dengan CRLF. Repository menetapkan `*.sh text eol=lf`.
Dari Git Bash, pulihkan file dari commit dengan aturan line ending repository:

```bash
git config core.autocrlf input
git restore scripts/manifold_inventory_readonly.sh
bash -n scripts/manifold_inventory_readonly.sh
```

Jika error tetap muncul, clone ulang repository setelah memastikan tidak ada
pekerjaan lokal yang belum disimpan. Jangan mengedit script menggunakan editor
yang memaksakan CRLF.

## Batasan audit

- Audit hanya boleh read-only.
- Jangan deploy atau build aplikasi.
- Jangan update firmware.
- Jangan menjalankan flight control.
- Jangan mengubah konfigurasi aircraft atau Manifold.
- Jangan menyimpan password, App Key, Advanced License, token, atau private
  key.
- Jangan menambahkan laporan dalam `runtime/` ke Git.
- Jangan melakukan force-push.
