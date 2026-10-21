# 输入与输出 Schema

## 1. 一条记录代表什么

建议一行代表一个菌落（通常是一个培养皿）。若一张照片中有多个皿或多个菌落，应拆成多行，并在 `image_path`、`sample_id`、`colony_roi` 中区分。

## 2. 核心输入列

| 字段 | 必填 | 说明 |
|---|---|---|
| `experiment_id` | 否 | 实验/批次编号；缺省为 `EXP`。不同实验的 CK 和菌饼直径分别计算 |
| `treatment` | 是 | 处理名。CK 可用 `CK`、`空白对照`、`对照`、`Control`，或用 `is_ck=true` 显式标记 |
| `replicate` | 建议 | 重复编号。默认期望每处理 3 个重复 |
| `sample_id` | 否 | 菌落/皿编号；缺省按行号生成 |
| `m1` … `m6` | 二选一 | 最多 6 个直径测量值，单位由 `unit` 决定 |
| `d1` … `d6` / `diameter_1` … `diameter_6` | 二选一 | 与 `m1..m6` 等价的别名 |
| `r1` … `r6` / `radius_1` … `radius_6` | 二选一 | 半径测量值；脚本会先 ×2 再作为直径处理 |
| `diameter_mm` | 二选一 | 已有单个平均直径时使用；也可用 `colony_diameter_mm`、`直径` |
| `unit` | 否 | `mm`、`cm` 或 `px`；缺省按 mm，列名含 `px` 时按 px |
| `scale_mm_per_px` | px 时必须 | 像素→毫米比例；优先于皿直径标定 |
| `dish_diameter_px` | px 时可用 | 培养皿像素直径；脚本用 `dish_diameter_mm / dish_diameter_px` 自动标定 |
| `dish_diameter_mm` | 否 | 培养皿实际直径，默认 86 mm；应与人工测量平面一致 |
| `reference_diameter_mm` | 否 | 标定参考直径，优先于 `dish_diameter_mm`；可填皿底内径、皿底外径或标尺长度 |
| `measurement_plane` | 否 | 人工测量平面：`bottom_inner` / `bottom_outer` / `agar_surface` / `ruler` / `unspecified` |
| `camera_angle` | 否 | 拍摄角度备注，例如 `top_down`、`about_60_deg` |
| `scale_axis` | 否 | 椭圆标定轴：`major`（默认）、`mean` 或 `minor` |
| `main_colony_mode` | 否 | 主菌落选择：`auto`（默认）、`center` 或 `largest` |
| `halo_mode` | 否 | 菌丝外沿扩展：`auto`（默认）、`tight` 或 `loose` |
| `plug_diameter_mm` | 否 | 菌饼直径；行内值优先 |
| `tip_color` | 否 | `yellow`/黄 → 6 mm，`blue`/蓝 → 8 mm；仅作缺省映射 |
| `edge_type` | 否 | `regular`（默认）或 `irregular`；影响 CV 复核阈值 |
| `equivalent_diameter_mm` | 否 | 图像面积换算的等效圆直径；用于和十字直径交叉校验 |
| `needs_review` / `auto_needs_review` | 否 | 输入或自动测量已标记复核时，会传递到最终结果 |
| `review_reasons` / `auto_review_reasons` | 否 | 复核原因文本 |
| `image_path` | 否 | 原始图片路径，便于回溯与后续训练 |
| `notes` | 否 | 备注 |

## 3. 测量值解析顺序

1. 优先识别 `m1..mN`、`d1..dN`、`diameter_1..dN`、`直径1..N`；按编号排序，最多使用前 6 个非空值。
2. 若只有 `r1..rN`/`radius_1..N`，把每个半径乘 2 后作为直径测量值。
3. 若没有编号列，则读取单个 `diameter_mm` 或 `diameter_px` 列。

数值允许带单位文本（脚本只提取数字），空值/`NA`/`NaN`/`-` 会被忽略。非数字值不会静默变成 0，但会减少有效测量数并触发复核标记。

## 4. 单位与标定

- `unit=mm`：直接使用。
- `unit=cm`：×10。
- `unit=px`：必须有 `scale_mm_per_px` 或 `dish_diameter_px`；否则该行跳过并写警告。
- `unit` 为空：按 mm 处理；若测量列名含 `px`，按 px 处理并同样要求标定。
- 同一行同时给出 `scale_mm_per_px` 和 `dish_diameter_px` 时，前者优先。

## 5. 菌饼直径解析优先级

1. 行内 `plug_diameter_mm`；
2. 行内 `tip_color` 映射（yellow→`--yellow-plug-mm`，blue→`--blue-plug-mm`）；
3. 命令行 `--plug-diameter-mm` / `-PlugDiameterMm`；
4. 默认 6 mm。

同一实验出现多个不同值时，取出现次数最多的值作为实验级菌饼直径，并写入警告和 `plug_source`。

## 6. 防效公式

`--formula-mode plug_corrected`（默认）：

```text
防效(%) = (CK平均直径 - 处理平均直径) / (CK平均直径 - 菌饼直径) × 100
```

`--formula-mode total_diameter`：

```text
抑制率(%) = (CK平均直径 - 处理平均直径) / CK平均直径 × 100
```

若分母 ≤ 0，该实验的处理防效输出为空，`efficacy_status=unavailable`，并写警告；不会输出 `Inf` 或伪造数值。

## 7. 输出文件

| 文件 | 内容 |
|---|---|
| `colony_measurements.csv` | 每个菌落：原始 `m1_mm..m6_mm`、`diameter_mm`、`sd_mm`、`cv`、`ecd_mm`、`n_colony_candidates`、`n_contaminants`、`ambiguous_main_colony`、`needs_review` |
| `treatment_summary.csv` | 每个处理：重复数、平均直径、SD、最小/最大值、复核标记 |
| `efficacy_results.csv` | 每个处理相对 CK 的防效、`formula_mode`、`plug_diameter_mm`、`denominator_mm`、复核标记 |
| `efficacy_result.json` | 完整参数、实验级 CK/菌饼、处理结果、警告和输出路径 |
| `run_log.txt` | 时间戳日志：读取行数、跳过项、标定方式、警告 |

## 8. 复核规则

- 有效测量值少于 `--expected-measurements`（默认 6）；
- 菌落直径 CV 超过阈值：常规 0.15，`irregular` 0.25；
- 菌落平均直径小于菌饼直径；
- 十字直径与 ECD 偏差超过 0.15；
- 重复数少于 3；
- 缺少 CK，或 CK 平均直径不足以计算所选公式的分母；
- 输入行本身带有 `needs_review=true`。

复核标记不阻止计算，但正式报告前必须逐条处理；使用 `--fail-on-review` 可让脚本在存在复核项时返回非零退出码。
