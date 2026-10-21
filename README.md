# 皿内菌落直径测量与防效统计（Petri Colony Efficacy）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)]()
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)]()

**批量测量培养皿内菌落直径、自动计算相对防效，并可导出 Excel 表格。**
支持十字交叉法取值、培养皿直径标定、菌饼直径（6/8 mm）校正、三重复处理汇总，以及"人工框取修正"与"继续训练模型"的完整流程。

> A toolkit for measuring fungal colony diameters inside Petri dishes and computing
> relative control efficacy (CK-based), with a local web GUI, Excel export, and a
> human-in-the-loop annotation workflow.

---

## 目录
- [功能特性](#功能特性)
- [三种使用方式](#三种使用方式)
- [安装](#安装)
- [快速开始](#快速开始)
- [输入数据准备](#输入数据准备)
- [输出说明](#输出说明)
- [防效计算公式](#防效计算公式)
- [精度与局限（重要）](#精度与局限重要)
- [用自己数据训练模型](#用自己数据训练模型)
- [常见问题](#常见问题)
- [仓库结构](#仓库结构)
- [许可](#许可)

---

## 功能特性

| 功能 | 说明 |
|---|---|
| **自动测量** | 识别培养皿 → 定位菌落边界 → 过质心十字交叉取 m1/m2 → 取均值 |
| **像素标定** | 用培养皿直径（默认 86 mm）自动换算 mm，无需手工标定 |
| **菌饼校正** | 菌饼 6 mm（黄枪头）/ 8 mm（蓝枪头），用于防效分母 |
| **三重复汇总** | 按"菌株_处理_重复"自动分组，以各自 CK 为基准算防效 |
| **人工修正** | 网页界面上可用 **圆形 / 多边形 / 自由涂抹** 重画边界；一键"满皿" |
| **质量提示** | 自动标注"需复核"（生长过小、对比度低、直径超皿径等） |
| **Excel 导出** | 测量结果 + 处理汇总（含防效） + 参数说明，共 3 个工作表 |
| **可自己训练** | 用户用**自己的图片 + 标注**训练边界细化模型，越用越准（[教程](docs/training.md)） |

---

## 三种使用方式

### 方式 A：图形界面（推荐给做实验的人）
浏览器界面，选择文件夹 → 自动测量 → 人工核对 → 导出 Excel。

```bat
python app\app.py  "D:\照片文件夹"
```

程序会启动本地服务并自动打开浏览器（默认 http://127.0.0.1:8765/）。

### 方式 B：作为 AI 助手的技能（Skill）

把本目录整体放到对应客户端的技能目录（文件夹名保持 `petri-colony-efficacy`）：

| 客户端 | 技能目录 |
|---|---|
| **WorkBuddy** | `%USERPROFILE%\.workbuddy\skills\` |
| **Codex** | `%USERPROFILE%\.codex\skills\`（或 `$env:CODEX_HOME\skills\`） |

放好后**重启客户端**，在对话里说"用 petri-colony-efficacy 技能测量这些照片"即可。
详细步骤见 [docs/workbuddy.md](docs/workbuddy.md)。

### 方式 C：命令行脚本（适合批处理/集成）

```bash
# 1) 只有人工测得的十字交叉值 → 直接算直径与防效
python scripts/compute_efficacy.py --input my_data.csv --output-dir out --plug-diameter-mm 6

# 2) 从照片批量测量（SAM + 细化模型，精度更高）
python scripts/run_sam_survey_v2.py --manifest plate_manifest.csv --output-dir out

# 3) 后处理：加质量标记（推荐）
python scripts/run_boundary_v8.py --src-run out --out-dir out_v8

# 4) 生成人工标注页面（圆形/多边形/涂抹）
python scripts/annot_draw_gallery.py --res-dir out_v8 --mapping out_v8/mapping.csv --out-html review.html
```

---

## 安装

```bash
git clone https://github.com/gphuang-star/petri-colony-efficacy.git
cd petri-colony-efficacy
pip install -r requirements.txt
```

**高精度模式（可选，强烈推荐）** 还需要：

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install git+https://github.com/facebookresearch/segment-anything.git
```

再下载 SAM 权重（约 358 MB，GitHub 放不下，请单独下载）：

```bash
# 放到 models/sam_vit_b_01ec64.pth
curl -L -o models/sam_vit_b_01ec64.pth \
  https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth
```

> 不想装 torch 也能用：不带 SAM 时程序自动退化为"径向纹理"快速引擎（0.2 秒/张，精度略低）。

---

## 快速开始

### 1. 准备数据
把培养皿照片放进一个文件夹（可含子文件夹），文件名建议体现"菌株_处理_重复"：

```
photos/
├── 水稻纹枯_CK_r1.jpg
├── 水稻纹枯_CK_r2.jpg
├── 水稻纹枯_嘧菌酯_r1.jpg
├── 层出镰刀菌_戊唑醇_r2.jpg
└── ...
```

### 2. 测量
```bash
python app/app.py "D:\photos"
```
或生成清单后走命令行流程（见方式 C）。

### 3. 核对与修正
浏览器打开结果页 → 绿圈=菌落边界，黄十字=量取线。
- 圈对了：直接看直径即可
- 圈错了：点「✚ 人工十字测量」打开**大图窗口**，先点横线两端、再点竖线两端，
  自动算出「横\竖」两条长度并取平均（可撤销/重画，Esc 取消）
- 细毛长满皿：点「按满皿(86mm)」
- 也可以点「输入直径…」直接填你量到的数
- 顶部有统计条与筛选（全部 / 仅看需复核 / 仅看已修正）

### 4. 导出
点「导出Excel表格」→ 得到 `菌落直径测量结果_日期.xlsx`。

---

## 输入数据准备

| 项目 | 要求 |
|---|---|
| 拍摄方式 | **俯拍**，镜头尽量垂直皿面；皿居中、完整入画 |
| 避免 | 强反光、拍皿底、一图多皿、遮挡 |
| 皿径 | 默认 86 mm（内外径基本一致）；如不同请改参数 |
| 菌饼 | 黄枪头 6 mm / 蓝枪头 8 mm |
| 重复 | 常见为 3 个重复；文件名区分 r1/r2/r3 |
| 格式 | jpg / jpeg / png / bmp / tif |

**只有人工测量值（无照片）时**：直接用 `compute_efficacy.py`，按
`assets/measurements-template.csv` 填表即可。字段说明见 [references/schema.md](references/schema.md)。

---

## 输出说明

### Excel（3 个工作表）

**① 测量结果**
`序号 | 图片文件名 | 文件夹 | 直径(mm) | 横(mm) | 竖(mm) | 状态 | 备注/提示 | 皿径 | 菌饼`

状态含义：
- `自动` —— 未发现异常
- `需复核` —— 生长过小 / 对比度低 / 直径超皿径等，建议人工确认
- `人工十字测量` —— 已用大图弹窗人工量取横/竖两条直径
- `人工修正` —— 已手动设定直径（满皿或直接输入）
- `失败` —— 未检测到培养皿或识别失败

**② 处理汇总**（文件名含"菌株_处理"时才生成）
`菌株/类别 | 处理 | 重复数 | 平均直径(mm) | CK平均直径(mm) | 防效(%)`

**③ 参数与说明** —— 本次设置、测量原理、公式、注意事项

### CSV（脚本模式）
- `diameters.csv` —— 逐图直径、m1/m2、识别方式、质量提示
- `efficacy.csv` —— 按处理汇总的平均直径与防效
- `qa/` —— 标注图（绿圈 + 十字），用于人工复核

---

## 防效计算公式

```
防效(%) = (CK平均直径 − 处理平均直径) / (CK平均直径 − 菌饼直径) × 100
```

等价于先把直径换成"生长增量"再套用经典公式
`(CK生长量 − 处理生长量) / CK生长量`。

- 若文献方法要求**不扣菌饼**，用 `--formula-mode total_diameter` 改为 `(CK − 处理)/CK × 100`
- 分母 ≤ 0 时程序**报错而不是输出负数/无穷大**
- 复现厂商/论文口径前，请先确认采用哪种公式

---

## 精度与局限（重要）

### 实测表现
| 场景 | 表现 |
|---|---|
| 边界清晰、中等大小菌落（多数常规图） | 与人工值 MAE ≈ 5 mm 以内，可直接用 |
| 细毛状菌丝 / 近满皿 / 深色培养基 / 与培养基同色 | **自动结果容易偏小**，必须人工复核 |

### 为什么难
细毛状菌丝的菌落与琼脂在照片上**几乎同色**（实测灰度差仅 0.3、饱和度差约 3），
任何无监督方法都难以稳定分离——这是数据本身的信息不足，不是简单的参数问题。

### 本项目的处理方式（human-in-the-loop）
1. 自动出结果，并把可疑的**标记为"需复核"**
2. 界面提供**圆形 / 多边形 / 涂抹**三种人工框取
3. 人工修正后的数值优先于自动值
4. 标注结果可导出为掩膜，**用于继续训练模型**

**请不要把自动结果不加核对地直接当作最终结论。**

---

## 用自己数据训练模型

**这是本项目推荐的使用方式**：每位使用者用自己的图片 + 标注，训练出贴合自己实验室条件的模型。

```
照片 → SAM 自动掩膜 → 边界细化模型(U-Net) → 最终边界
                            ↑ 这一层由你的标注训练
```

**实测**（53 张标注、3 折交叉验证、CPU 不到 1 分钟）：

| 方案 | MAE | 中位误差 |
|---|---|---|
| 仅 SAM 自动掩膜 | 33.8 mm | 30.3 mm |
| **加入细化模型** | **11.6 mm** | **6.3 mm** |

### 三步走

```bash
# 1) 用程序跑一遍（会自动保存自动掩膜到 照片目录/菌落测量输出/auto_masks/）
python app/app.py "D:\photos"

# 2) 用标注模式打开 → 把边界画对 → 导出 CSV（不加 --annotate 只有简洁结果页）
python app/app.py --annotate "D:\\photos"
#    然后在网页里画边界 → 导出CSV，再构建数据集
python scripts/build_mask_dataset.py \
  --annotations "D:\photos\人工框取.csv" \
  --images "D:\photos" \
  --base-mask-dir "D:\photos\菌落测量输出\auto_masks" \
  --out-dir "D:\dataset"

# 3) 训练（会打印"自动 vs 细化 vs 人工"的交叉验证对比）
python scripts/train_mask_refiner.py --dataset "D:\dataset" --out "models/refine_unet.pt"
```

把生成的 `refine_unet.pt` 覆盖到 `models/` 即可生效，**代码无需改动**。

### 要点
- **50 张左右**就能看到明显提升；建议累积到 100–300 张
- 只标"圈错的"图会让模型**总往大扩**；**程序会自动把「状态=自动」的图作为保持样本补进训练集**（零额外操作，`--auto-identity` 可关）
- 脚本会打印留出验证的提升比例，**为负就不要用这个模型**
- 不需要显卡；CPU 几分钟即可

📖 完整说明（含需要多少数据、常见坑、FAQ）：[**docs/training.md**](docs/training.md)

## 常见问题

**Q：一定要装 torch / SAM 吗？**
A：不必。没有 SAM 时自动退化为纹理法（快但精度低），适合先跑通流程。

**Q：皿径不是 86 mm？**
A：改参数即可（GUI 的 bat 里 `set COLONY_DISH_MM=`，或命令行 `--dish-diameter-mm`）。

**Q：菌饼是 8 mm 的？**
A：把菌饼直径设为 8，防效分母自动跟着变。

**Q：结果里出现负防效 / 超过 100%？**
A：负防效表示处理组比 CK 长得更大；超过 100% 通常是处理组直径小于菌饼。
程序**不会**悄悄截断，会原样输出并提示复核。

**Q：想只用人工测量值算防效？**
A：用 `compute_efficacy.py`，一行命令即可，不需要照片。

---

## 仓库结构

```
petri-colony-efficacy/
├── SKILL.md                  # 作为 Codex Skill 使用时的入口说明
├── README.md
├── LICENSE
├── requirements.txt
├── app/                      # 图形界面程序（本地网页版）
│   ├── app.py                #   主程序：选文件夹 → 测量 → 浏览器核对 → 导出 Excel
│   ├── engine_sam.py         #   高精度引擎：SAM + 细化模型
│   ├── excel_export.py       #   Excel 导出（3 个工作表，自动算防效）
│   ├── ui_template.py        #   界面模板（圆形/多边形/涂抹标注）
│   └── lib/                  #   测量与标定核心
├── scripts/                  # 命令行工具
│   ├── compute_efficacy.py           # 防效/直径计算（CSV）
│   ├── measure_colony_image.py       # 单图测量（多引擎）
│   ├── radial_texture_engine.py      # 径向纹理边界引擎
│   ├── run_sam_survey_v2.py          # SAM 批量测量
│   ├── run_boundary_v8.py            # 后处理 + 质量标记
│   ├── annot_draw_gallery.py         # 生成人工标注页面
│   ├── extract_pptx_plates.py        # 从 PPT 批量提取皿图并按标注命名
│   ├── build_mask_dataset.py         # 构建训练集
│   ├── train_mask_refiner.py         # 训练边界细化模型
│   └── ...                           # 候选生成 / 无序标签 / 评估
├── models/
│   └── refine_unet.pt        # 边界细化模型（1.9 MB）
├── references/               # 数据规范、图像方法、文献与工具、训练约定
├── assets/                   # CSV 模板
├── docs/                     # 详细文档（用法 / 拍摄规范 / 精度 / 自己训练 / 上传 GitHub）
└── examples/                 # 示例数据与期望输出
```

---

## 作者与引用

**作者：gphuang**

如果本项目对你的研究有帮助，可以这样引用（若已上传 GitHub，请把链接换成本仓库地址）：

```bibtex
@software{gphuang_petri_colony_efficacy,
  author = {gphuang},
  title  = {Petri Colony Efficacy: 皿内菌落直径测量与防效统计},
  year   = {2026},
  url    = {https://github.com/gphuang-star/petri-colony-efficacy}
}
```

仓库内已提供 [CITATION.cff](CITATION.cff)，GitHub 会自动显示 "Cite this repository"。

## 许可

本项目采用 [MIT License](LICENSE)（版权所有 © 2026 gphuang）。

使用到的第三方组件请遵守其各自许可：
- [Segment Anything](https://github.com/facebookresearch/segment-anything)（Apache-2.0）及其权重
- PyTorch（BSD-3）、OpenCV（Apache-2.0）、NumPy（BSD-3）、Pillow（HPND）、openpyxl（MIT）

> 若用于发表，请同时引用你所采用的防效公式来源（见 [references/literature-and-tools.md](references/literature-and-tools.md)）。
