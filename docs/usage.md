# 详细使用说明

## 一、三种使用场景

| 场景 | 用什么 | 需要照片吗 |
|---|---|---|
| 已有全部人工测量值，只想算直径/防效 | `scripts/compute_efficacy.py` | 不需要 |
| 有照片，想自动测 + 人工核对 | `app/app.py`（图形界面） | 需要 |
| 批量处理/集成到自己的流程 | `scripts/` 下命令行脚本 | 需要 |

---

## 二、图形界面（app/app.py）

### 启动
```bash
python app/app.py "D:\你的照片文件夹"
```
不带参数运行会弹出文件夹选择对话框。

程序会：
1. 加载模型（首次约 10 秒）
2. 逐张测量（高精度模式 20–60 秒/张；快速模式 0.2 秒/张）
3. 启动本地网页服务并自动打开浏览器

### 界面（两种模式）
| 模式 | 启动方式 | 内容 |
|---|---|---|
| **结果页**（默认） | `python app/app.py <文件夹>` | 图片 + 直径 + 状态；按满皿 / 输入直径 / 导出 Excel。**无任何训练元素** |
| **标注页** | `python app/app.py --annotate <文件夹>` | 在上面基础上增加：圆形/多边形/涂抹绘制、标签、备注、导出CSV（给要训练模型的人用） |

标注页的界面元素说明：
| 元素 | 含义 |
|---|---|
| 绿圈 | 识别到的菌落边界 |
| 黄十字 | 十字交叉量取线（m1 / m2） |
| 统计条 | 共几张 / 自动 / 需复核 / 识别失败 / 已人工修正 |
| 筛选 | 全部、未处理、自动+需复核、识别失败、已人工修正 |
| 圆形 | 按住鼠标拖出一个圆 |
| 多边形 | 依次点击边界点，点回第一个点闭合；顶点可拖动 |
| ✏️涂抹 | 按住鼠标自由涂抹（不规则/长条形菌落） |
| 一键满皿 | 直接按皿径（默认 86 mm）计——细毛长满皿时用 |
| 未处理·全部按满皿 | 批量把没处理的图按满皿设置 |

### 导出
- **导出Excel表格**：3 个工作表（测量结果 / 处理汇总含防效 / 参数说明）
- **导出CSV**：含形状坐标（归一化），可用于训练

---

## 三、命令行流程

### 1) 人工值 → 直径与防效
```bash
python scripts/compute_efficacy.py \
  --input examples/sample_measurements.csv \
  --output-dir out \
  --plug-diameter-mm 6 \
  --formula-mode plug_corrected
```
参数：
- `--plug-diameter-mm`：菌饼直径（6 或 8）
- `--formula-mode`：`plug_corrected`（默认，扣菌饼）或 `total_diameter`（不扣）

### 2) 照片批量测量（需要清单 CSV）
清单格式见 `assets/plate-manifest-template.csv`。

```bash
# 快速（纹理法）
python scripts/run_boundary_v8.py --src-run <某个测量结果目录> --out-dir out_v8

# 高精度（SAM + 细化）
python scripts/run_sam_survey_v2.py --manifest plate_manifest.csv --output-dir out_sam
python scripts/run_boundary_v8.py --src-run out_sam --out-dir out_v8
```

`run_boundary_v8.py` 会额外输出质量标记列：
`needs_manual`、`review_reason`、`radial_mm`、`radial_ratio`、`solidity`、`gray_contrast`。

### 3) 人工标注页面
```bash
python scripts/annot_draw_gallery.py \
  --res-dir out_v8 --mapping out_v8/mapping.csv \
  --out-html review.html --title "复核"
```
浏览器打开 `review.html`，工具条有四种画法：

| 按钮 | 用法 |
|---|---|
| 圆形 | 拖一个圆 |
| 多边形 | 逐点围边界，点首点闭合 |
| 涂抹 | 按住拖出菌落区域 |
| **📏 两点测距** | 点两下画一条直径；再点两下画第二条，实时显示「第1条 / 第2条 / 平均」。移动鼠标有 **4× 放大镜**，点击会 **自动吸附到最近的强边缘**（工具栏可关） |

**复核首选「两点测距」**：它就是十字交叉法的人工版——第一条画横径、第二条画竖径，
结果直接写入导出 CSV 的 `人工直径mm / 人工m1mm / 人工m2mm`。

画完点「导出CSV」。

### 4) 按难度挑图（只标值得标的）

```bash
python scripts/select_hard_cases.py   --src-run out_v8 --out-dir selection   --limit 40 --max-extreme 0.20 --html
```

输出 `selection.csv` + `review.html`。容易的图直接跳过，中等的优先标，
极端的限量标（默认不超过 20%，否则模型会学成"一律放大"）。

### 5) 从 PPT 批量提取皿图
```bash
python scripts/extract_pptx_plates.py \
  --pptx 试验数据.pptx --out-dir extracted \
  --plug-mm 6 --col-override "18=CK,嗜硫小红卵菌,竞品戊唑醇"
```
每个幻灯片当作一个批次，图片按页面文字标注自动命名。

---

## 四、参数速查

| 参数 | 默认 | 说明 |
|---|---|---|
| 皿径 | 86 mm | 用于像素标定；内外径基本一致 |
| 菌饼 | 6 mm | 黄枪头 6 / 蓝枪头 8；用于防效分母 |
| 交叉方向 | 2 条 | 过质心作水平 + 垂直两条直径 |
| 处理重复 | 3 | 每个处理 3 个皿（按中位数/均值汇总） |

---

## 五、人工修正原则
1. **人工值优先于自动值**（程序里就是这么实现的）
2. 细毛状/近满皿：优先用「一键满皿」
3. 边界不规则：用「涂抹」；需要精确描点用「多边形」
4. 修正完记得**导出**；页面改动会自动存到浏览器本地（localStorage）
