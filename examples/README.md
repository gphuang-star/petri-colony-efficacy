# 示例数据

本目录提供**最小可运行示例**，用于验证安装是否成功。

## 1. 只用人工测量值算防效（无需照片）

`sample_measurements.csv` 是一份人工记录的十字交叉值（每条处理 3 个重复）。

```bash
python scripts/compute_efficacy.py \
  --input examples/sample_measurements.csv \
  --output-dir examples/out \
  --plug-diameter-mm 6
```

预期输出：
- `examples/out/colony_measurements.csv` —— 逐皿直径、均值、SD、CV
- `examples/out/treatment_summary.csv` —— 各处理平均直径
- `examples/out/efficacy_results.csv` —— 相对 CK 的防效（%）

## 2. 照片测量

`sample_plate_manifest.csv` 展示了图片清单的格式；把 `image_path` 换成你自己的
照片路径即可（照片请勿提交到仓库）：

```bash
python scripts/run_sam_survey_v2.py \
  --manifest examples/sample_plate_manifest.csv \
  --output-dir examples/out_sam
```

## 3. 生成人工标注页面

```bash
python scripts/annot_draw_gallery.py \
  --res-dir examples/out_sam \
  --mapping examples/sample_plate_manifest.csv \
  --out-html examples/review.html
```
