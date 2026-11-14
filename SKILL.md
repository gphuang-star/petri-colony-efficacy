---
name: petri-colony-efficacy
description: 培养皿内菌落直径测量与防效计算；支持皿盖数字编号对应处理、人工核对后一键生成改名副本或直接改名，保留逐图来源和实验清单。提供十字交叉测量、86 mm 皿径标定、6/8 mm 菌饼校正、处理汇总及图像标注。用于皿内菌落生长抑制试验；数字识别候选和菌落边缘均需人工核对。
metadata:
  short-description: 皿盖编号对应、一键改名与菌落测量防效
  author: gphuang
  version: 7.0.0
---

# Petri Colony Efficacy

将培养皿图像或十字交叉测量数据整理为可复核的菌落直径、处理均值和相对 CK 的防效结果。

## Required Outcomes

每次测量和防效计算至少产出以下文件；仅整理编号/改名时，产出逐图对应表、实验清单和改名事务记录，不要求先运行测量：

- `colony_measurements.csv`：每个菌落的原始测量值、均值、SD、CV 和复核标记；
- `treatment_summary.csv`：每个处理（含 CK）的重复数、平均直径和 SD；
- `efficacy_results.csv` 与 `efficacy_result.json`：按实验分别以 CK 为基准计算的防效及所用菌饼直径；
- `run_log.txt`：运行参数、跳过项、冲突项和警告。

## Core Rules

1. 培养皿直径默认 `86 mm`，用于把像素测量换算为 mm；已有 mm 测量值时不需要标定。
2. 一个菌落最多记录 6 个直径测量值；先对该菌落所有有效测量值取算术平均，作为该菌落的直径。
3. 边缘不规则时公式不变，但必须保留原始 6 个测量值、SD、CV 和 `needs_review`。常规菌落默认 CV 阈值 0.15；标记为 `irregular` 的菌落放宽到 0.25。图像测量同时输出等效圆直径（ECD）和最小外接圆直径，与十字直径偏差超过 0.15 时复核。
4. 处理平均直径是该处理全部重复菌落直径的算术平均。CK 平均直径同样按实验分别计算。
5. 菌饼直径 `plug_diameter_mm` 可为 6 或 8 mm：黄色枪头默认 6 mm，蓝色枪头默认 8 mm。行内值优先于实验值，实验值优先于命令行默认值。
6. 默认使用扣菌饼防效公式：

   `防效(%) = (CK平均直径 - 处理平均直径) / (CK平均直径 - 菌饼直径) × 100`

   它等价于先把每个直径转成生长增量 `(直径 - 菌饼直径)`，再套用文献常见的 `(CK生长 - 处理生长)/CK生长`。若论文方法要求不扣菌饼，使用 `--formula-mode total_diameter` 改为 `(CK - 处理) / CK × 100`。两种模式的依据和文献见 [references/literature-and-tools.md](references/literature-and-tools.md)。

   若所选公式的分母 ≤ 0，该实验不能计算防效，必须报错而不是输出负数或无穷大。

7. 每个实验必须能找到 CK。缺少 CK、重复数不足、测量值个数不足或存在明显异常值时，仍保留可计算的结果，但在相应行设置 `needs_review=true` 并写入日志。
8. 主菌落识别：默认 `--main-colony-mode auto`，优先选择靠近皿中心、面积较大、形状较规则的连通域作为主菌落。其他达到最小面积的连通域作为疑似杂菌/非主菌落候选，从主菌落直径中排除，并在 QA 图中用红色 `CONT` 标出。检测到杂菌候选或主菌落选择存在歧义时标记 `needs_review`。
9. 菌丝外沿扩展支持 `--halo-mode auto|tight|loose`。`tight` 用于绿圈包含空白琼脂、皿底凸起、气泡或绿圈大于菌丝的图片；`loose` 用于绿色边框未囊括所有菌丝的图片。新数据先用 `auto`，必要时人工指定，不要把 tight/loose 写死为全局规则。
10. 人工测量平面必须记录。若人工量的是皿底/底部直径，而图片是斜拍，二者不在同一平面，不能默认用皿外径 86 mm 直接等同。用 `reference_diameter_mm` 指定与人工测量同一参考特征的直径，并记录 `measurement_plane`（如 `bottom_inner`、`bottom_outer`、`agar_surface`、`ruler`）。图像标定默认用椭圆长轴，透视明显（`perspective_ratio < 0.85`）时自动标记复核。
11. 皿盖编号表示本批处理，重复号另存。OCR 仅作未确认候选；使用用户核对后的编号—处理对应，不从拍摄顺序推断重复、不强制把模糊数字归入已知处理。默认一键生成改名副本，直接改名保留原图来源与撤销记录；后续测量以确认后的清单为准。

## 路径约定（重要）

本文档中的 `<SKILL_DIR>` 一律指 **本 SKILL.md 所在目录的绝对路径**。
不同客户端的默认位置：

| 客户端 | 技能目录 |
|---|---|
| WorkBuddy（CodeBuddy 内核） | `%USERPROFILE%\.workbuddy\skills\petri-colony-efficacy` |
| Codex | `$env:CODEX_HOME\skills\petri-colony-efficacy`（无该变量时用 `%USERPROFILE%\.codex\skills\petri-colony-efficacy`） |
| 手动安装 / 其他 Agent | 你自己解压/克隆到的目录 |

执行命令前，把 `<SKILL_DIR>` 替换成上表对应的真实路径即可。

## Workflow

### 皿盖写数字：编号对应与一键改名

用户在皿盖上写 `1、2、3、11` 等编号，或要求优化批量改名时，先读 [references/number-label-workflow.md](references/number-label-workflow.md)。软件先填写本批“编号—处理全名”，显示数字识别候选和图片，核对后预览并一键生成改名副本；也支持直接改名和撤销。

- 对应表模板：`assets/treatment-code-template.csv`。一个编号对应完整处理条件，浓度不同需区分；CK 明确填写为 `CK`。
- `1-2` 表示处理 `1` 的重复 `2`；只有处理号时重复未知，不补成 `r1/r2/r3`。同皿复拍不能增加独立重复数。
- skill 的命令行入口：`scripts/number_label_workflow.py scan/suggest/apply/undo`。`suggest` 离线读取数字候选，保留未确认状态；`apply` 使用明确确认的逐图 CSV，与软件共用命名逻辑。软件界面另提供框选编号区域重试，其真实皿盖准确率尚待验证。
- 改名记录输出 `mapping.csv`、`plate_manifest.csv` 与事务日志；原有规范文件名/清单可直接进入以下测量流程，无须再次改名。

### CSV-only：首选、最稳定

1. 参考 `assets/measurements-template.csv` 准备数据；列定义见 [references/schema.md](references/schema.md)。
2. 运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\run_pipeline.ps1" `
  -MeasurementsCsv "C:\path\measurements.csv" `
  -OutputDir "C:\path\output" `
  -PlugDiameterMm 6 `
  -FormulaMode plug_corrected
```

3. 检查 `colony_measurements.csv` 中的 `needs_review` 和 `review_reasons`，必要时修正输入后重跑。

### 图像辅助测量：实验性，需要先标定

1. 参考 `assets/plate-manifest-template.csv` 建立图片清单；详细参数见 [references/image-method.md](references/image-method.md)。
2. 运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\run_pipeline.ps1" `
  -PlateManifestCsv "C:\path\plate_manifest.csv" `
  -OutputDir "C:\path\output" `
  -PlugDiameterMm 8
```

3. 检查 `qa\` 下的标注图。若皿边缘或菌落边缘识别错误，使用 `scripts/measure_colony_image.py` 的 `--dish-diameter-px`、`--center`、`--colony-roi` 或 `--threshold` 手动修正，或改用 CSV-only 模式人工录入测量值。
4. 白/浅色菌丝、琼脂对比低或照片有反光时，尝试 `--method texture` 或 `--method vs`。批量比较不同方法可用：

```powershell
python "<SKILL_DIR>\scripts\generate_candidates.py" `
  --manifest "C:\path\plate_manifest.csv" `
  --output-dir "C:\path\candidates" `
  --methods auto,light,vs,texture `
  --rays 4
```

5. 自动直径只能作为初判。后续用“图片 + 人工直径/分割掩膜”训练专用模型时，按 [references/training-data.md](references/training-data.md) 积累数据；未标定前不要把自动直径当作最终结果。

### 无序每皿标签：人工值无法对应到具体图片时

如果人工表只能确认“相邻两个测量值属于同一个皿”，但不能确认它对应哪张图片，不要把 pair 强行绑定到 r1/r2/r3。使用：

```powershell
python "<SKILL_DIR>\scripts\prepare_unordered_labels.py" `
  --manual-csv "C:\path\manual_values.csv" `
  --manifest "C:\path\plate_manifest.csv" `
  --output-dir "C:\path\unordered_labels"

python "<SKILL_DIR>\scripts\evaluate_unordered_predictions.py" `
  --predictions "C:\path\predictions.csv" `
  --labels "C:\path\unordered_labels\unordered_labels.csv" `
  --output-dir "C:\path\unordered_eval"
```

- `unordered_labels.csv` 记录每个皿的 pair 均值和图片集合，`mapping_status=unknown`。
- `evaluate_unordered_predictions.py` 输出处理均值误差、排序后 MAE、最优一对一匹配 MAE，以及 `assignment_suggestions.csv`。
- `assignment_suggestions.csv` 只是“图片 ↔ 人工 pair”的候选匹配，必须人工确认后才能当作逐图标签。
- 训练时使用置换不变损失（排序匹配、Hungarian/Sinkhorn 或集合级聚合），不要把未知映射当成随机固定标签。

### 人工两点测距（复核首选）

自动结果被标 `needs_review` 时，生成标注页人工量：

```powershell
python "<SKILL_DIR>\scripts\annot_draw_gallery.py" `
  --res-dir "C:\path\out_v8" `
  --mapping "C:\path\out_v8\mapping.csv" `
  --out-html "C:\path\review.html"
```

打开 `review.html`，工具条提供四种方式：

| 按钮 | 用法 |
|---|---|
| 圆形 | 拖一个圆 |
| 多边形 | 逐点围出边界，点首点闭合 |
| 涂抹 | 按住拖出菌落区域（可开橡皮） |
| **📏 两点测距** | **点两下画一条直径；再点两下画第二条，自动显示「第1条 / 第2条 / 平均」** |
| 一键满皿 | 直接按皿径 86 mm 计 |

「两点测距」就是十字交叉法的人工替代：第一条画横径，第二条画竖径，
页面实时显示两条长度和平均值，与自动结果并列对比。

点「导出CSV」得到 `人工框取.csv`（含人工直径、m1、m2、形状JSON），可回灌流水线。

### 按难度挑图（只标值得标的）

一批跑完后不要逐张翻，先分层：

```powershell
python "<SKILL_DIR>\scripts\select_hard_cases.py" `
  --src-run "C:\path\out_v8" `
  --out-dir "C:\path\selection" `
  --limit 40 --max-extreme 0.20 --html
```

- **容易**（径向纹理与 SAM 直径一致）→ 不标
- **中等**（SAM 少圈 1.2~3 倍，或有复核标记）→ **优先标**
- **极端**（只圈到菌饼，比值 > 3）→ 限量标；占比超过 20% 会把模型带偏

输出 `selection.csv`（每张的分类与原因）与 `review.html`（只含被挑中的图，可直接用两点测距标注）。

如果某批结果里没有 `radial_mm`（例如旧版本只存了 SAM/细化结果），先补算（不需要重跑 SAM）：

```powershell
python "<SKILL_DIR>\scripts\add_radial_estimate.py" --src-run "C:\path\old_out" --out-dir "C:\path\old_out_radial"
```

### 复核标记太多怎么办

`needs_review` 是"任一条命中就标"，常见的误伤项：

| 触发原因 | 默认阈值 | 放宽参数 |
|---|---|---|
| 直径 CV 超阈值 | 0.15 | `-CvThreshold 0.30` |
| 不规则菌落 CV | 0.25 | `-IrregularCvThreshold 0.40` |
| 十字直径 vs 等效圆偏差 | 15% | `-EcdReviewThreshold 0.30` |
| 照片透视明显（短轴/长轴） | < 0.85 | 无参数，只能重拍俯拍或人工量 |
| 培养皿/菌落检测置信度低 | < 0.35 | 无参数，人工量 |
| 检测到疑似杂菌候选 | > 0 | 无参数，看 QA 图确认 |

放宽示例（只减少提示，不改变测量值）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\run_pipeline.ps1" `
  -PlateManifestCsv "C:\path\manifest.csv" `
  -OutputDir "C:\path\out" `
  -PlugDiameterMm 6 `
  -CvThreshold 0.30 -IrregularCvThreshold 0.40 -EcdReviewThreshold 0.30
```

**排查方法**：直接看结果里 `colony_measurements.csv` 的 `review_reasons` 列，
会写明每张图具体因为哪一条被标。

### 边界形状（重要）

- 默认**不做圆/椭圆拟合**，输出的是菌落**真实不规则轮廓**。
- 只有显式加 `--circle-fit`（run_boundary_v8）或 `--regularity-cv-threshold 0.15`（measure_colony_image）才会变成标准圆/椭圆。
- SAM 之后会自动加载 `models/refine_unet.pt` 做边界细化（`run_sam_survey_v2.py`，可用 `--no-refine` 关闭），与封装版程序一致。

## Non-Negotiable Checks

- 不要把一张图片的结果当作整个实验或田间的最终结论。
- 不要丢弃原始 6 个测量值或只保留均值。
- 不要让不同实验共用 CK 或共用菌饼直径。
- 不要把负防效、超过 100% 的防效或 CK 异常值悄悄截断；原样输出并标记复核。
- 图像自动测量后必须查看标注图和掩膜；无法确认边缘时使用人工测量值。

## References

- [references/number-label-workflow.md](references/number-label-workflow.md)：皿盖数字编号、处理对应对话框、一键改名/撤销、逐图确认 CSV 和清单衔接。
- [references/schema.md](references/schema.md)：输入列别名、单位、缺失值处理与输出字段。
- [references/image-method.md](references/image-method.md)：皿/菌落检测参数、十字交叉采样和人工兜底。
- [references/training-data.md](references/training-data.md)：后续图像标注、无序每皿标签、数据积累和模型训练约定。
- [references/literature-and-tools.md](references/literature-and-tools.md)：防效公式的文献依据，以及 ColonyArea、Colonyzer、OpenCFU、ImageJ/Leaf Doctor 等工具可借鉴的逻辑。

### 从 PPTX 批量提取皿图并按标注命名

当实验图片整理在 PPT 里（每页一个批次，图片下/上带处理文字标注）时，用：

```powershell
python "<SKILL_DIR>\scripts\extract_pptx_plates.py" `
  --pptx "C:\path\some.pptx" `
  --out-dir "C:\path\extracted" `
  --plug-mm 6
```

- 每个幻灯片视为一个批次，图片放入 `slideNN/`，命名为 `NN_序号_主题_处理.扩展名`。
- 处理名按“与图片列 x 中心最近”的文字标注匹配；主题优先取图片左侧的行标签，其次取页面标题。
- 若某页图片列没有文字标注（处理名只写在数据表里），用 `--col-override "18=CK,嗜硫小红卵菌,竞品戊唑醇"` 指定按列顺序的处理名，多页用 `;` 分隔。
- 同时输出 `mapping.csv`（提取核对表，人工确认命名）与 `plate_manifest.csv`（可直接喂给 `run_sam_survey_v2.py`）。
- 提取后务必先看 `mapping.csv` 或拼版图确认命名，再进入测量；一键同名/错位会直接污染结果。

