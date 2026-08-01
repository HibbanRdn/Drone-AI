# Offline tracking and gap-analysis source

Folder ini mempertahankan source yang sudah ada untuk:

- video + SRT discovery;
- tiled plant-center inference;
- registration dan mosaic;
- one-to-one observation fusion menjadi unique plants;
- manual atau operator-authorized AI-assisted analysis ROI;
- row reconstruction, expected plant points, missing-point candidates, dan gap
  grouping;
- QA evidence dan review-video export.

Source ini adalah workflow post-processing offline. Live PSDK runtime di
src/gap_plot_ai tetap melaporkan plant count per frame; ia belum mengklaim
unique physical plant count atau final gap result secara live.

Model default diambil langsung dari root repo:

~~~text
models/source/plant_center_manual_v1_b0_best.pt
models/source/plot_segmenter_b4_selected_best.pt
~~~

Contoh full pipeline dari root repo:

~~~bash
cd tools/b0_manual_v1_video_demo
python -m postprocess.full_pipeline \
  --flight-dir /path/to/video-and-matching-srt \
  --device cpu \
  --frame-stride 5 \
  --batch-size 1 \
  --seg-imgsz 1280 \
  --seg-conf 0.10 \
  --seg-iou 0.85 \
  --seg-morphology closing_3 \
  --output-dir ../../runtime/tools/b0_manual_v1_video_demo/full_run
~~~

Raw video/SRT dan hasil run tidak masuk repository. Nilai tracking/gap canonical
berasal dari source postprocess/config.py dan disalin untuk inspeksi ke
models/configs/postprocess.json. Nilai segmentasi full-pipeline 0.10/0.85 dengan
closing_3 adalah operating point workflow terminal yang sudah ada; live PSDK
runtime memakai operating point tersendiri di config/app.yaml.
