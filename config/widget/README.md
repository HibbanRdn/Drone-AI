# Widget Config

Native app v27 melakukan register widget dari:

```text
config/widget/en_big_screen/
config/widget/cn_big_screen/
```

Folder ini berasal dari working build tree Manifold dan diperlukan oleh `src/psdk/main.cpp::InitWidget()`.

File legacy `config/widget/widget_config.json` masih dipertahankan dari baseline Git sebelumnya. Untuk source Manifold v27, gunakan `en_big_screen/` dan `cn_big_screen/` sebagai widget config utama sampai file legacy direview dan dihapus secara sengaja.
