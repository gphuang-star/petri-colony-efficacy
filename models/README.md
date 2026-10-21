# 模型文件说明

| 文件 | 大小 | 是否随仓库提供 | 说明 |
|---|---|---|---|
| `refine_unet.pt` | 约 1.9 MB | ✅ 已包含 | 边界细化模型，配合 SAM 掩膜使用 |
| `sam_vit_b_01ec64.pth` | 约 358 MB | ❌ **需自行下载** | SAM ViT-B 权重（GitHub 单文件上限 100 MB） |

## 下载 SAM 权重

```bash
# 方式一：curl
curl -L -o models/sam_vit_b_01ec64.pth \
  https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth

# 方式二：浏览器直接下载后放进 models/ 目录
#   https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth
```

下载后目录应为：

```
models/
├── refine_unet.pt
└── sam_vit_b_01ec64.pth
```

## 没有 SAM 权重会怎样？

程序仍可运行，会自动退化为"径向纹理"引擎：
- 速度：约 0.2 秒/张（SAM 模式 20–60 秒/张）
- 精度：常规菌落可用；**细毛状菌丝、近满皿会明显偏小**

## 用自己数据训练 refine_unet.pt

见 [../references/training-data.md](../references/training-data.md)，
或直接运行：

```bash
python scripts/build_mask_dataset.py
python scripts/train_mask_refiner.py
```
