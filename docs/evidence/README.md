# Evidence

Folder ini berisi evidence kecil yang dikurasi dari snapshot Manifold v27. Ini bukan dump raw snapshot.

## Isi

| Folder | Isi |
|---|---|
| `v26/` | proof blocker v26 dan ringkasan forensic/provenance v26 |
| `v27/` | patch/provenance/T01/T02/T03 v27 |
| `runtime/` | blackbox evidence runtime v26 yang menunjukkan PSDK/liveview aktif |

## Tidak Dimasukkan

- DPK binary;
- installed runtime mirror;
- full runtime sessions;
- TensorRT engine binary;
- full forensic source snapshot;
- evidence lama yang redundant atau kualitasnya tidak seragam.

Aturan membaca evidence:

```text
source PASS != DPK built
fixture PASS != deployed runtime
v26 runtime proof != v27 runtime proof
```
