# Source Provenance

Dokumen ini menjelaskan asal-usul source yang sekarang menjadi canonical repository source. Tujuannya agar developer berikutnya tidak perlu menebak hubungan antara GitHub, development di Manifold, v26, v27, DPK, dan runtime terinstall.

## Ringkasan Provenance

```text
Git baseline lama
  -> development berlanjut di Manifold
  -> v26 source
  -> build/package
  -> DPK 00.01.00.26
  -> runtime v26 terinstall
  -> Blocker-001 ditemukan
  -> v27 source fix
  -> T01/T02/T03 PASS
  -> v27 belum build/package/deploy
  -> v27 dikonsolidasikan kembali ke repository ini
```

## Sumber Yang Dibandingkan Saat Konsolidasi

| Sumber | Peran | Status |
|---|---|---|
| GitHub/local baseline | History resmi sebelum konsolidasi | HEAD `3d92c342c36ae5912c9d71047fab6e43c936f080` |
| Manifold `vnext-v26` | Baseline source v26 | Source sebelum fix Blocker-001 |
| Manifold `vnext-v27` | Latest development source | Basis canonical source v27 |
| Manifold working build tree | Scaffold build/package terakhir | Berguna untuk config/widget/scripts/DPK context |
| DPK v26 | Artifact terakhir yang berhasil dibuat | `00.01.00.26` |
| Installed runtime v26 | Runtime terakhir di device | v27 belum terinstall |

Raw snapshot Manifold dipakai sebagai evidence saat konsolidasi. Developer berikutnya tidak perlu memiliki snapshot tersebut untuk memahami project, karena source canonical, provenance, evidence kecil, dan dokumentasi sudah ada di repository ini.

## Hash Penting

| Item | Nilai |
|---|---|
| Git baseline sebelum konsolidasi | `3d92c342c36ae5912c9d71047fab6e43c936f080` |
| v26 source tree | `5fae7a659e1e2dcd3b484912eb8862b72a09ed5515217a9931329ff38829ef45` |
| v27 source tree | `5cea0ab045b039139ff9047e202de73c8b208e87a690d7c6925d3e3d867adf34` |
| v26 finalizer | `15a3cd2b7b8f5eb526bc12966fd5036109fdbf5018288f66c5ddca8488d1e844` |
| v27 finalizer | `4b753d74ca84d0e8dd27150349622a48015ba1ca54a2fd166f0c3d1eaadf32eb` |
| v26 DPK | `6b9a74d3671ed41f8ef1271468b12578e745367c8b1b971a3437cc47a1fc410c` |
| installed v26 native binary | `1e1045e774e68f9af3f82a2bc249c2b2bdb9bba0c2cdf9abcd40bcaa2bb12f59` |

## Mengapa GitHub Sebelumnya Belum Lengkap

Sebelum handover, GitHub/local repository sudah memiliki PSDK liveview app, model artifacts, script build/deployment, dan dokumentasi awal. Namun beberapa development penting dilakukan langsung di Manifold tanpa berada dalam Git worktree project. Akibatnya, stack finalization Manifold belum ada di GitHub baseline.

File Manifold-only yang sekarang dikonsolidasikan:

```text
src/gap_plot_ai/finalizer.py
src/gap_plot_ai/geospatial_v2.py
src/gap_plot_ai/registration_writer.py
src/gap_plot_ai/shp_writer.py
src/gap_plot_ai/spatial.py
```

Selain itu, beberapa file existing seperti `src/gap_plot_ai/runtime.py`, `src/gap_plot_ai/worker.py`, `src/gap_plot_ai/tensorrt_backend.py`, `src/psdk/main.cpp`, `config/app.yaml`, dan `config/live.yaml` disinkronkan ke latest v27 source.

## v26 Ke v27

Diff source v26 -> v27 yang terverifikasi pada snapshot adalah:

```text
src/gap_plot_ai/finalizer.py
```

Perubahan tersebut memperbaiki `V26-SOFTWARE-BLOCKER-001`, yaitu bug coordinate back-projection pada `analyze_gaps()`.

Status v27 setelah fix:

```text
T01 coordinate inverse: PASS
T02 gap semantics: PASS
T03 positive gap E2E fixture: PASS
v27 build: BELUM
v27 package/DPK: BELUM
v27 deploy/runtime hardware validation: BELUM
```

## Canonical Source Setelah Konsolidasi

Source canonical untuk development berikutnya adalah:

```text
src/
config/
scripts/
dpk/
CMakeLists.txt
```

Repository ini sengaja tidak menyimpan folder seperti `source-final`, `v27-final`, atau full raw snapshot. Provenance dijaga melalui:

- source canonical;
- dokumen ini;
- hash penting;
- evidence terkurasi di `docs/evidence/`;
- dokumentasi build/deployment dan known issues.

### Hasil comparison canonical versus raw v27

Audit final membandingkan seluruh `src/gap_plot_ai/`, `src/psdk/main.cpp`, dan config raw v27 terhadap repository:

| Kategori | Hasil |
|---|---|
| **IDENTICAL** | 23 file source/config raw v27 byte-identical dengan canonical |
| **EXPECTED DIFFERENCE** | `finalizer.py`, `geospatial_v2.py`, `registration_writer.py`, `runtime.py`, dan `main.cpp` hanya berbeda pada trailing whitespace |
| **REPOSITORY-SPECIFIC** | Widget ditempatkan di `config/widget/` dan byte-identical dengan working build tree Manifold; template credential, example config, scripts, test, packaging, dan dokumentasi dipertahankan dari repository |
| **POTENTIAL PROBLEM** | Tidak ada kehilangan atau perubahan algoritma yang ditemukan pada comparison final |

Hash raw v27 tetap dicatat di atas. Normalisasi trailing whitespace tidak mengubah behavior runtime atau fix Blocker-001.

## Aturan Interpretasi

| Pertanyaan | Jawaban |
|---|---|
| Source development terbaru di mana? | `src/` dan `config/` pada repository ini |
| Runtime terakhir yang pernah terinstall versi apa? | v26 |
| Apakah v27 sudah deployed? | Belum |
| Apakah raw snapshot masih wajib? | Tidak untuk memahami handover; hanya arsip historis |
| Apakah DPK v27 sudah ada? | Belum |
