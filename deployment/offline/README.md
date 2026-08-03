# Offline deployment templates

`scripts/create_offline_deployment.py` merender file di direktori ini ke paket
final. Generator menolak working tree kotor, branch yang salah, origin yang
bukan `HibbanRdn/Drone-AI`, checkout PSDK yang bukan exact official tag 3.16.0,
serta artefak deployment yang dilarang.

Jangan menjalankan template langsung dari repository karena placeholder commit
belum dirender. Paket final baru dibuat setelah diff disetujui dan di-commit.
