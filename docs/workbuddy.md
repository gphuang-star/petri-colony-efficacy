# 在 WorkBuddy 中使用 petri-colony-efficacy

> 适用版本：WorkBuddy 桌面版（CodeBuddy 内核），Windows。
> WorkBuddy 的 Skill 格式与 Codex 相同：一个文件夹 + `SKILL.md`。

---

## 一、前置条件

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10 / 11 64 位 |
| Python | 3.8 及以上（`python --version` 能输出版本号） |
| 磁盘 | 高精度模式约需 2 GB（含 358 MB SAM 权重） |

> WorkBuddy **不自带 Python**。如果目标电脑没有 Python，建议改用免安装包
> `菌落测量v2.0.6_高精度版`（双击 `启动程序.bat`，不需要装任何东西）。

---

## 二、安装 Skill

### 方式 1：手动放置（最直接）

1. 下载仓库 **Code → Download ZIP**
2. 解压，得到文件夹 `petri-colony-efficacy`
3. 把它整个放到：

```
C:\Users\<你的用户名>\.workbuddy\skills\petri-colony-efficacy
```

最终应当是这个结构：

```
C:\Users\<用户名>\.workbuddy\skills\petri-colony-efficacy\
├── SKILL.md
├── scripts\
├── references\
├── assets\
├── app\
└── models\
```

> ⚠️ 文件夹名必须是 `petri-colony-efficacy`，并且 `SKILL.md` 要**直接**在这个文件夹里
> （不要再套一层 `petri-colony-efficacy/petri-colony-efficacy/`）。

### 方式 2：让 WorkBuddy 自己装

在 WorkBuddy 对话里说：

> 帮我安装这个 Skill：https://github.com/gphuang-star/petri-colony-efficacy

它会调用内置的 `marketplace-skill-installer` 完成安装。

### 方式 3：git clone（方便以后更新）

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.workbuddy\skills" | Out-Null
git clone https://github.com/gphuang-star/petri-colony-efficacy.git `
  "$env:USERPROFILE\.workbuddy\skills\petri-colony-efficacy"
```

以后更新：`git -C "$env:USERPROFILE\.workbuddy\skills\petri-colony-efficacy" pull`

---

## 三、安装 Python 依赖

```powershell
cd "$env:USERPROFILE\.workbuddy\skills\petri-colony-efficacy"
python -m pip install -r requirements.txt
```

**高精度模式（可选，推荐）：**

```powershell
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
python -m pip install git+https://github.com/facebookresearch/segment-anything.git

curl.exe -L -o models\sam_vit_b_01ec64.pth `
  https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth
```

> 不装 torch 也能跑，程序会自动退化为"径向纹理"快速模式（约 0.2 秒/张，精度略低）。

---

## 四、自检

```powershell
cd "$env:USERPROFILE\.workbuddy\skills\petri-colony-efficacy"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\check-tools.ps1"
```

期望输出里能看到 `numpy OK`、`cv2 OK`、`PIL OK`。

---

## 五、让 WorkBuddy 识别到它

1. **完全退出 WorkBuddy**（托盘图标右键退出，确保进程结束）
2. 重新打开
3. 新开一个对话，问一句验证：

> 你现在有哪些 skill？

应该能看到 `petri-colony-efficacy`。

---

## 六、怎么调用

Skill 是按 `SKILL.md` 里的 `description` **自动匹配**的。直接说需求即可；
为了保险，可以明确点名。

### 例 1：批量测照片 + 算防效

> 用 **petri-colony-efficacy** 技能，把 `D:\2026-10-15\照片` 下所有培养皿照片测一遍。
> 皿径 86 mm，菌饼 6 mm。按文件名里的「菌株_处理_r1」分组，以 CK 为基准算防效，
> 结果输出到 `D:\out`。

### 例 2：只有人工测的十字交叉值

> 用 **petri-colony-efficacy** 技能，我只有人工测的十字值，表在 `D:\测量.xlsx`，
> 帮我算每个处理的平均直径和防效，菌饼用 6 mm。

### 例 3：检查哪些图需要复核

> 用 **petri-colony-efficacy** 技能，检查 `D:\out` 里哪些图 `needs_review=true`，
> 把原因逐条列出来。

### 例 4：重新训练边界模型

> 用 **petri-colony-efficacy** 技能，用我标注好的数据重新训练边界细化模型。

---

### 复核被标「需复核」的图

让 WorkBuddy 生成标注页：

> 用 petri-colony-efficacy 技能，把 `D:\结果` 生成人工复核页面 `review.html`

打开后点 **📏 两点测距**：在菌落边缘点两下画一条直径，再点两下画第二条，
页面会显示「第1条 / 第2条 / 平均」。移动鼠标会出现 **4× 放大镜**，
点击会 **自动吸附到最近的边缘**，点得更准；不需要吸附可取消勾选「吸附边缘」。
最后点「导出CSV」保存。

## 七、WorkBuddy 会做什么

```
读取 SKILL.md
  → 判断走「CSV-only」还是「图像测量」
  → 调用 scripts\run_pipeline.ps1 或 compute_efficacy.py
  → 产出：
       colony_measurements.csv     每皿原始值 + SD/CV + 复核标记
       treatment_summary.csv       每处理均值
       efficacy_results.csv/json   防效（含所用菌饼直径）
       run_log.txt                 运行参数与警告
```

规则写在 `SKILL.md` 里：皿径 86 mm、菌饼 6/8 mm、防效公式、CV 阈值、
必须标 `needs_review` —— AI 不会自己发挥。

---

## 八、常见问题

| 现象 | 原因 / 解决 |
|---|---|
| 对话里找不到这个 skill | 文件夹名不对、`SKILL.md` 套了两层、或没重启 WorkBuddy |
| 提示没有权限访问照片目录 | 在 WorkBuddy 的文件/沙箱权限里放行该目录 |
| `python 不是内部或外部命令` | 未安装 Python 或未加入 PATH |
| `ModuleNotFoundError: cv2` | 没装依赖，执行 `pip install -r requirements.txt` |
| 高精度模式报错找不到权重 | 按第三节下载 `models\sam_vit_b_01ec64.pth` |
| 处理很慢 | 高精度模式 20–60 秒/张属正常；可改用快速模式 |
| 想给不懂电脑的人用 | 直接用免安装包，别用 Skill |

---

## 九、能力边界

- **能**：批量测量、按 CK 算防效、导出 Excel、生成复核清单、用标注数据重训模型。
- **不能**：替你做最终结论。`SKILL.md` 明确要求自动结果必须人工复核边缘与异常值。
- **注意**：自动测量是"初判"，正式报告前请重点看 `needs_review=true` 的图片。