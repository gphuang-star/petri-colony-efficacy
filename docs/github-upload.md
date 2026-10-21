# 上传到 GitHub 的完整教程

本仓库已按 GitHub 规范整理好（含 `README.md`、`LICENSE`、`.gitignore`、`requirements.txt`），
你按下面任选一种方式上传即可。

> ⚠️ **上传前最重要的一件事**：不要提交 SAM 权重 `sam_vit_b_01ec64.pth`（358 MB）。
> GitHub 单文件上限 100 MB，会被拒收。`.gitignore` 已经帮你排除了它，别人按
> `models/README.md` 自行下载即可。仓库本身只有约 2 MB。

---

## 方式一：网页上传（最简单，零命令行）

适合：上传一个新仓库，之后不再频繁改动。

1. 打开 https://github.com/new
2. **Repository name** 填 `petri-colony-efficacy`
3. 选 **Public**（公开）或 **Private**（私有）
   - 建议勾选 **Add a README file**（否则下面的"上传文件夹"更省事）
4. 点 **Create repository**
5. 在新仓库页面点 **Add file → Upload files**
6. 把 `petri-colony-efficacy` 文件夹里的**所有内容**（不是文件夹本身）拖进去
   - ⚠️ 注意：GitHub 网页**不支持拖整个文件夹**，需要把它里面的文件/子文件夹一起选中拖入
   - 子文件夹（`app/` `scripts/` `docs/` 等）可以直接拖
7. 下方 **Commit changes** 里写一句说明，例如 `Initial release v2.0.0`，点 **Commit changes**

上传完成后，仓库首页会自动显示 `README.md` 的内容。

---

## 方式二：Git 命令行（推荐，可反复更新）

### 1. 安装 Git
Windows：https://git-scm.com/download/win （一路下一步即可）
安装后在任意位置右键 → **Git Bash Here**，或打开 PowerShell 输入 `git --version` 验证。

### 2. 配置身份（只需一次）
```bash
git config --global user.name "你的名字"
git config --global user.email "你的邮箱"
```

### 3. 建立远程仓库
在 https://github.com/new 创建空仓库（**不要**勾选 Add README），记下地址，例如：
`https://github.com/gphuang-star/petri-colony-efficacy.git`

### 4. 本地初始化并推送
```bash
cd "C:\path\to\petri-colony-efficacy"   # 改成你本地的实际路径

git init
git add .
git status                      # 检查一遍：不应出现 *.pth / *.jpg 大文件
git commit -m "Initial release v2.0.0"

git branch -M main
git remote add origin https://github.com/gphuang-star/petri-colony-efficacy.git
git push -u origin main
```

推送时会要求登录：推荐用 **Personal Access Token**（见下方"常见问题"）。

### 5. 以后更新
```bash
git add .
git commit -m "描述这次改了什么"
git push
```

---

## 方式三：GitHub Desktop（图形化，适合不熟悉命令的人）

1. 下载安装 https://desktop.github.com/
2. 登录 GitHub 账号
3. 菜单 **File → Add local repository** → 选择本仓库文件夹
4. 若提示不是仓库，点 **create a repository** → 直接 **Create**
5. 左下角填写 Summary（如 `Initial release v2.0.0`）→ 点 **Commit to main**
6. 点顶部 **Publish repository** → 选择 Public/Private → **Publish**

---

## 常见问题

**Q1：推送时提示要密码 / 认证失败？**
GitHub 已不支持账号密码。请创建 **Personal Access Token**：
`GitHub → 右上角头像 → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token`
勾选 `repo` 权限，生成后**复制保存**（只显示一次）。
推送时用户名填 GitHub 用户名，密码处**粘贴这个 token**。

**Q2：提示 `file is 358 MB; this exceeds GitHub's file size limit of 100 MB`？**
说明 SAM 权重被加进来了。执行：
```bash
git rm --cached models/sam_vit_b_01ec64.pth
```
确认 `.gitignore` 里有 `models/*.pth` 与 `!models/refine_unet.pt`，然后重新 commit。

**Q3：不小心提交了照片/实验结果？**
```bash
git rm -r --cached outputs/            # 只从 Git 移除，本地文件保留
git commit -m "remove experiment data"
```
（历史记录里仍有；若已推送且必须清除，需要用 `git filter-repo` 或联系 GitHub 支持。）

**Q4：中文文件名会乱码吗？**
把 `core.quotepath` 关掉，显示更友好：
```bash
git config --global core.quotepath false
```

**Q5：想同时发布"免安装的图形界面程序"（600+ MB）？**
不要放进仓库，改用 **Releases**：
`仓库 → Releases → Draft a new release → 填版本号 → 在 Attachments 里上传 zip`。
然后在 README 里加上下载链接。

**Q6：想让别人通过 pip 安装 / 被 AI 助手调用？**
- pip 安装：需要额外添加 `pyproject.toml` 或 `setup.py`（当前仓库是脚本集合，直接 clone 即可用）
- 作为 Codex Skill：把整个文件夹放到 `~/.codex/skills/petri-colony-efficacy/`，
  助手会读取 `SKILL.md` 按规范调用

---

## 建议在 README 顶部补上的信息
- 换个更具体的仓库名（如 `petri-colony-efficacy`）
- 在 GitHub 仓库页右侧 **About** 填写一句话简介和 topics：
  `mycology` `colony-diameter` `efficacy` `image-analysis` `plant-pathology`
- 若用于论文，在 README 里加 **Citation** 段落，方便别人引用
