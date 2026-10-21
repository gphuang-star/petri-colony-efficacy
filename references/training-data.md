# 后续图像训练数据规范

目标是在积累足够“图片 + 人工直径/掩膜”后，训练专用分割或直径回归模型，替换当前的启发式图像测量。训练前先把本 skill 的 CSV 流程作为数据管理和人工真值入口。

## 1. 目录结构

```text
training/
├── images/                 # 原始图片，不改名不压缩
├── masks/                  # 每个菌落的二值分割掩膜，文件名与图片对应
├── labels.csv              # 图片、菌落、处理、重复、人工直径
├── plates.csv              # 皿级元数据：皿直径、菌饼直径、拍摄距离/设备
└── splits.json             # train/val/test 划分（按实验或皿划分，避免泄漏）
```

## 2. labels.csv 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `image_path` | 是 | 原始图片相对路径 |
| `experiment_id` | 是 | 实验/批次 |
| `plate_id` | 是 | 培养皿唯一编号 |
| `treatment` | 是 | 处理名，CK 也要注明 |
| `replicate` | 是 | 重复编号 |
| `dish_diameter_px` | 建议 | 人工确认的皿像素直径 |
| `dish_center_x` / `dish_center_y` | 建议 | 皿中心像素坐标 |
| `colony_center_x` / `colony_center_y` | 建议 | 菌落中心像素坐标 |
| `colony_roi` | 可选 | 菌落候选框 `x,y,w,h` |
| `mask_path` | 可选 | 人工分割掩膜路径 |
| `m1_mm` … `m6_mm` | 是 | 人工测量的直径值；至少两条垂直方向 |
| `diameter_mm` | 是 | 上述测量值均值 |
| `equivalent_diameter_mm` | 可选 | 人工掩膜面积换算值 |
| `edge_type` | 是 | `regular` / `irregular` |
| `plug_diameter_mm` | 是 | 本次实验实际菌饼直径，6 或 8 mm |
| `annotator` | 建议 | 标注人 |
| `notes` | 可选 | 边界情况说明 |

## 3. 标注规则

1. 在 ImageJ/Fiji 或本 skill 的标注图中，先用 86 mm 皿直径完成像素标定。
2. 至少量取两条相互垂直的直径；不规则菌落建议 3 条方向（最多 6 个直径值）。
3. 直径量到菌丝边缘的最外沿，不包含皿壁反光；若边缘呈锯齿状，记录每个方向的真实端点。
4. 菌饼直径另存为元数据，不要把菌饼边缘当作菌落边缘。
5. 每张图片同时保存分割掩膜；掩膜用于训练分割模型和计算 ECD。
6. 对反光、贴壁、边缘不完整、两个菌落粘连等异常情况单独备注，不要强行给一个精确直径。

## 4. 无序每皿标签（pair-to-image mapping unknown）

人工测量常常是统一测完 6 个值，只能确认相邻两个值属于同一个皿，无法确认这两个值对应哪张图片。此时：

- 不要把 6 个值按顺序强行分配给 r1/r2/r3；这会制造错误的监督信号。
- 把每个处理的标签表示为“3 个无序的皿均值”集合，把图片表示为“3 张无序的图片”集合。
- 使用 `scripts/prepare_unordered_labels.py` 生成 `unordered_labels.csv`，其中 `mapping_status=unknown`。
- 使用 `scripts/evaluate_unordered_predictions.py` 计算：
  - 处理均值误差；
  - 排序后 MAE（预测排序 vs 标签排序）；
  - 最优一对一匹配 MAE（允许任意置换）；
  - `assignment_suggestions.csv`（图片 ↔ pair 的候选匹配，需人工确认）。

训练目标可选：

1. **集合级回归**：共享图片编码器 + 置换不变聚合（DeepSets/attention），直接预测处理均值或 3 个直径的集合。
2. **置换不变损失**：对预测值和标签集合分别排序后计算 MAE；或用 Hungarian/Sinkhorn 在训练中学习最优匹配。
3. **两阶段**：先训练分割/直径候选模型，再用候选匹配生成逐图标签，人工确认后转为有监督逐图训练。

评价时优先看处理均值 MAE、排序后 MAE、最优匹配 MAE、处理排序相关性和 CK 是否最低；不要用错误的逐图对应关系计算 per-image MAE。

## 5. 划分与防泄漏

- 按 `experiment_id` 或 `plate_id` 划分 train/val/test，禁止同一皿的图片同时出现在训练和测试集。
- 若同一处理有多个时间点，同一皿的所有时间点必须留在同一划分。
- 测试集要包含不同批次、不同拍摄设备/光照和至少一部分不规则边缘样本。

## 5. 建议的模型输出与评价

推荐优先做分割 + 几何后处理，而不是直接回归单个直径：

- 分割：输出菌落掩膜，得到 Area、ECD、Feret、轮廓；
- 中心：质心或距离变换峰值；
- 直径：在掩膜上用与人工相同的十字/多射线协议测量；
- 可选回归头：直接预测 `diameter_mm`，作为 ECD 的补充。

评价指标：

- 分割：IoU / Dice；
- 直径：MAE、RMSE、Bland–Altman 偏差和一致性界限；
- 重复性：同一皿多次推理的 CV；
- 业务指标：处理排序 Spearman/Kendall、CK 是否排最后、前三名命中率、防效与人工防效的相关性。

## 6. 与当前 skill 的衔接

- 当前先运行 CSV-only 流程确定人工真值和防效；
- 图像模式生成 `measurements_auto.csv`，与人工 `measurements.csv` 逐行对比；
- 用 `cross_vs_ecd_deviation`、CV 和人工复核结果作为难例挖掘依据；
- 达到稳定 MAE 和排序一致性后，再把训练模型的输出接入 `measurements_auto.csv`，同时保留人工复核开关。


## 7. 候选选择器（当前最优先的训练路线）

手工阈值在不同图片上会给出不同方向的错误（有时漏菌丝、有时包含琼脂），但候选池里往往存在正确掩膜。建议：

1. 对每张图片生成候选掩膜：`auto/light/vs/texture` × `halo_mode=auto/tight/loose`。
2. 用人工 QA 备注作为弱标签，例如：
   - `too_small`（遗漏菌丝）；
   - `too_large`（包含琼脂/皿壁/凸起）；
   - `good`（贴合菌丝外沿）；
   - `false_contaminant`（把气泡/凸起当成杂菌）；
   - `missed_contaminant`（漏掉真正杂菌）。
3. 训练一个分类器/打分器，输入图像特征 + 候选特征（面积占比、中心偏移、圆度、fill ratio、纹理对比、边界梯度等），输出候选排名或“最佳候选”。
4. 先用无序每皿标签评价处理均值和排序，等模型稳定后再把最佳候选转成逐图标签并继续训练分割模型。
5. 如果用户能提供 50–100 张人工分割掩膜，优先训练 U-Net/DeepLab 类主菌落分割模型，再在掩膜上做几何测量。
