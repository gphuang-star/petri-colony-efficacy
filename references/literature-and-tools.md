# 文献与现有工具可借鉴逻辑

本文件记录构建和校准本 skill 时参考的公开工具与已发表实验流程，用于说明具体计算或图像处理决定的来源。核对日期：2026-10-05。

## 1. 防效公式的两种常见写法

### 1.1 不扣除菌饼：文献中最常见的写法

```text
抑制率(%) = (DC - DT) / DC × 100
```

`DC` 和 `DT` 分别是 CK（对照）和处理组的平均菌落直径（包含初始菌饼）。代表文献明确采用该写法：

- PMC13040968（2026）：`% Inhibition = DC-DT/DC × 100`，菌落沿两条垂直轴测量，三次重复。
- PMC12938698（2026）：用 6 mm 打孔器取菌饼，十字法测量含菌饼的菌落直径，`[(Negative control colony diameter - Treatment colony diameter)/(Negative control colony diameter)] × 100%`。
- PMC13514241（2026）：使用 8 mm 菌饼，在两条垂直方向测量并“减去接种菌饼直径”，`PIMG = [(C - T)/C] × 100%`。
- PMC9398913（2022）：6 mm 接种盘，引用 Vincent 的 `(对照生长 - 处理生长)/对照生长 × 100%`。

### 1.2 扣除菌饼：本 skill 默认，也是本任务给出的公式

```text
防效(%) = (DC - DT) / (DC - P) × 100
```

其中 `P` 是菌饼直径（6 mm 或 8 mm）。它与 1.1 并不矛盾，而是先把每个菌落直径转换为生长增量：

```text
GC = DC - P
GT = DT - P
防效 = (GC - GT) / GC × 100 = (DC - DT) / (DC - P) × 100
```

PMC13514241 的做法正是先在每条垂直方向“减去接种菌饼直径”，再使用 1.1 公式；因此当文献中的 `C` 和 `T` 指扣除菌饼后的生长直径时，公式形式与本 skill 的 `plug_corrected` 完全一致。PMC13610318 则指出，在比较两个时间点的生长速率时初始菌饼直径会自然抵消，说明采用哪种分母取决于分析对象是“含菌饼的总直径”还是“从菌饼边缘算起的生长增量”。

本 skill 因此同时支持：

- `plug_corrected`（默认）：`(DC - DT) / (DC - P) × 100`；
- `total_diameter`：`(DC - DT) / DC × 100`。

输出 CSV/JSON 始终记录 `formula_mode`、`plug_diameter_mm` 和 `denominator_mm`，避免论文复现时混淆。

## 2. 十字交叉法与测量次数

- PMC13610318：每皿测两条垂直菌落直径并取平均。
- PMC13040968：沿两条垂直轴测径向生长并取平均直径。
- PMC13514241：每处理在两条垂直方向测量，三次重复。

这些流程支持“两条垂直直径 → 2 个测量值取均值”的最小形式。本任务允许最多 6 个测量值，本 skill 因此把测量次数做成可配置，并用 SD/CV 处理边缘不规则的菌落，而不是假定菌落一定是规则圆。

## 3. 菌饼直径

- 6 mm：PMC12938698 使用 `φ = 6 mm` 枪头取菌饼；PMC9398913 使用 6 mm 接种盘。
- 8 mm：PMC13514241 使用 8 mm 菌饼；PMC13040968 使用 6 mm 菌饼。

因此 6/8 mm 必须作为实验级元数据记录。`tip_color` 只是便捷映射（黄→6，蓝→8），实际值应以本次实验记录为准。

## 4. 公开图像工具可借鉴的逻辑

| 工具 | 发表/仓库 | 可借鉴点 | 本 skill 的落地方式 |
|---|---|---|---|
| ColonyArea | Guzmán et al., PLoS ONE 2014, DOI 10.1371/journal.pone.0092444；GitHub `Turku-BioImaging/ColonyArea` | 阈值分割 + 面积定量；对不规则菌落比单纯直径更稳健 | Lab 色差 + Otsu 分割，计算等效圆直径（ECD）作为交叉校验 |
| Colonyzer | Lawless et al., BMC Bioinformatics 2010, DOI 10.1186/1471-2105-11-287；GitHub `CnrLwlss/Colonyzer` | 固体琼脂上菌落的自动图像定量；生长随时间变化 | 预留多时间点/生长速率扩展；当前先稳定单时间点直径 |
| OpenCFU | Geissmann, PLoS ONE 2013, DOI 10.1371/journal.pone.0054072；GitHub `qgeissmann/OpenCFU` | 圆形物体检测、去噪、尺寸过滤 | Hough 圆检测培养皿 + 面积/中心位置过滤菌落 |
| ImageJ/Fiji | Schneider et al., Nature Methods 2012, DOI 10.1038/nmeth.2089 | 标尺标定、手工直径/面积测量、掩膜 QA | 自动测量失败时的人工兜底；所有输出保留掩膜供 QA |
| Leaf Doctor | Pethybridge & Nelson, Plant Disease 2015, DOI 10.1094/PDIS-03-15-0319-RE | 便携式图像严重度评估、依赖人工校准 | 自动结果必须 `needs_review`，不能替代人工确认 |
| Assess 2.0 | 商用/学术病害严重度评估工具 | 标准面积与严重度校准 | 本 skill 不依赖 Assess；外部结果可作为 `measurement_json`/CSV 输入 |

## 5. 对本 skill 的直接结论

1. 默认使用扣菌饼公式，但同时提供不扣菌饼的已发表公式，避免方法学争议。
2. 十字法测量值全部保留；均值用于计算，SD/CV 用于识别不规则边缘。
3. 图像自动测量除十字直径均值和 ECD 外，还输出最小外接圆直径（Feret 近似），三者差异过大时标记复核。
4. 培养皿 86 mm 仅作为默认标定值；像素直径和 mm/px 都可覆盖。
5. 持续积累“图片 + 人工直径/掩膜”后，再把自动测量升级为经训练的模型。
