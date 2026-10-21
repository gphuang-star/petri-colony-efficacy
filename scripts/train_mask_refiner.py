# -*- coding: utf-8 -*-
"""训练边界细化模型（U-Net），把"自动掩膜"修正为"人工标注掩膜"。

用法
  # 先用 build_mask_dataset.py 生成数据集，然后：
  python scripts/train_mask_refiner.py --dataset D:\\dataset --out models/refine_unet.pt

输出
  --out 指定的模型文件（可直接替换程序里的 models/refine_unet.pt）
  控制台会打印交叉验证的"自动 vs 细化 vs 人工"误差对比

依赖
  pip install torch torchvision   （CPU 版即可）
"""
from __future__ import annotations
import argparse, math, os, sys, time
from pathlib import Path


def _early_libs():
    """在导入 numpy 之前处理 --libs，保证内嵌 Python 也能用到正确的 numpy/torch。

    形如：  --libs D:\\run\\samlibs   或   --libs=D:\\run\\samlibs;D:\\other
    """
    vals = []
    argv = sys.argv
    for i, a in enumerate(argv):
        if a == "--libs" and i + 1 < len(argv):
            vals.append(argv[i + 1])
        elif a.startswith("--libs="):
            vals.append(a.split("=", 1)[1])
    for v in vals:
        for d in str(v).split(os.pathsep):
            if d and d not in sys.path:
                sys.path.insert(0, d)


_early_libs()

import numpy as np          # noqa: E402  （必须在 _early_libs 之后）
import cv2                  # noqa: E402


def _need_torch(extra_libs=""):
    if extra_libs:
        for d in str(extra_libs).split(os.pathsep):
            if d and d not in sys.path:
                sys.path.insert(0, d)
    try:
        import torch, torch.nn as nn, torch.nn.functional as F
        return torch, nn, F
    except Exception as e:
        print("错误：未安装 PyTorch。请先执行：\n  pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu")
        print("原始错误：", e)
        sys.exit(1)


def build_unet(torch, nn, F, ci=4, base=16):
    class UNet(nn.Module):
        def __init__(s):
            super().__init__()
            def blk(i, o):
                return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(),
                                     nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU())
            s.e1 = blk(ci, base); s.e2 = blk(base, base*2); s.e3 = blk(base*2, base*4); s.b = blk(base*4, base*8)
            s.u3 = nn.ConvTranspose2d(base*8, base*4, 2, 2); s.d3 = blk(base*8, base*4)
            s.u2 = nn.ConvTranspose2d(base*4, base*2, 2, 2); s.d2 = blk(base*4, base*2)
            s.u1 = nn.ConvTranspose2d(base*2, base, 2, 2);   s.d1 = blk(base*2, base)
            s.out = nn.Conv2d(base, 1, 1)
        def forward(s, x):
            e1 = s.e1(x); e2 = s.e2(F.max_pool2d(e1, 2)); e3 = s.e3(F.max_pool2d(e2, 2)); b = s.b(F.max_pool2d(e3, 2))
            return s.out(s.d1(torch.cat([s.u1(s.d2(torch.cat([s.u2(s.d3(torch.cat([s.u3(b), e3], 1))), e2], 1))), e1], 1)))
    return UNet()


def main():
    ap = argparse.ArgumentParser(description="训练边界细化模型")
    ap.add_argument("--dataset", required=True, help="build_mask_dataset.py 生成的目录（含 *.npz）")
    ap.add_argument("--out", default="models/refine_unet.pt", help="输出模型路径")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--size", type=int, default=128, help="网络输入边长")
    ap.add_argument("--base", type=int, default=16, help="U-Net 基础通道数")
    ap.add_argument("--folds", type=int, default=3, help="交叉验证折数（1=不验证，直接全量训练）")
    ap.add_argument("--cv-only", action="store_true", help="只做交叉验证，不训练最终模型")
    ap.add_argument("--seed", type=int, default=0, help="随机种子，保证可复现")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--threads", type=int, default=0, help="CPU 线程数，0=自动")
    ap.add_argument("--libs", default="", help="额外的库目录（内嵌 Python 时用来指定 torch 所在目录，多个用 ; 分隔）")
    a = ap.parse_args()

    torch, nn, F = _need_torch(a.libs)
    torch.manual_seed(a.seed)     # 固定种子，保证每次交叉验证结果可复现
    if a.threads > 0: torch.set_num_threads(a.threads)

    # rglob：连同 identity/ 子目录里的"保持样本"一起读（此前只读了顶层，白扔一半数据）
    files = sorted(Path(a.dataset).rglob("*.npz"))
    if len(files) < 4:
        print(f"错误：{a.dataset} 里只有 {len(files)} 个样本，至少需要 4 个。")
        print("请先用标注页导出 CSV，再运行 build_mask_dataset.py 生成数据集。")
        sys.exit(1)
    print(f"载入 {len(files)} 个样本…")

    X, Y, MM, BASE = [], [], [], []
    for f in files:
        z = np.load(f)
        # 兼容两种数据集命名：新版用 base，旧版用 sam
        base = z["base"] if "base" in z.files else z["sam"]
        img, man = z["img"], z["man"]
        mmpp = float(z["mmpp"]); S = a.size
        sc = S / img.shape[0]
        X.append(np.concatenate([cv2.resize(img, (S, S), interpolation=cv2.INTER_AREA).astype(np.float32)/255.0,
                                 cv2.resize(base, (S, S), interpolation=cv2.INTER_NEAREST)[..., None].astype(np.float32)], axis=2)
                 .transpose(2, 0, 1))
        Y.append((cv2.resize(man, (S, S), interpolation=cv2.INTER_NEAREST) > 0).astype(np.float32)[None])
        MM.append(mmpp/sc)
        # 注意：base 是原分辨率(如512)的掩膜，必须用 mmpp 而不是 mmpp/sc，
        # 否则基线会被放大 (原尺寸/S) 倍，导致"提升"虚高。
        BASE.append(2*math.sqrt(max(int((base > 0).sum()), 1)/math.pi)*mmpp)
    gt = [2*math.sqrt(max(int((y[0] > 0).sum()), 1)/math.pi)*m for y, m in zip(Y, MM)]
    base_mae = float(np.mean([abs(b-g) for b, g in zip(BASE, gt)]))
    print(f"基线（自动掩膜）与人工的 MAE = {base_mae:.1f} mm")

    def dice_loss(p, t):
        p = torch.sigmoid(p)
        return 1 - ((2*(p*t).sum((1, 2, 3)) + 1) / ((p*p).sum((1, 2, 3)) + (t*t).sum((1, 2, 3)) + 1)).mean()

    def train_model(idx, epochs):
        net = build_unet(torch, nn, F, base=a.base)
        opt = torch.optim.Adam(net.parameters(), lr=a.lr)
        Xt = torch.tensor(np.stack([X[i] for i in idx])); Yt = torch.tensor(np.stack([Y[i] for i in idx]))
        for ep in range(epochs):
            net.train(); perm = torch.randperm(len(idx))
            for k in range(0, len(idx), a.batch):
                j = perm[k:k+a.batch]
                opt.zero_grad(); out = net(Xt[j])
                loss = F.binary_cross_entropy_with_logits(out, Yt[j]) + dice_loss(out, Yt[j])
                loss.backward(); opt.step()
            if (ep + 1) % max(1, epochs//5) == 0:
                print(f"    epoch {ep+1}/{epochs}")
        return net

    t0 = time.time(); results = []
    if a.folds and a.folds > 1 and len(files) >= a.folds * 2:
        rng = np.random.RandomState(a.seed); order = rng.permutation(len(files))
        folds = np.array_split(order, a.folds)
        for i in range(a.folds):
            va = list(folds[i]); tr = [j for j in order if j not in va]
            print(f"  折 {i+1}/{a.folds}：训练 {len(tr)} / 验证 {len(va)}")
            net = train_model(tr, a.epochs); net.eval()
            with torch.no_grad():
                pr = torch.sigmoid(net(torch.tensor(np.stack([X[j] for j in va])))).numpy()[:, 0] > 0.5
            for k, j in enumerate(va):
                d = 2*math.sqrt(max(int(pr[k].sum()), 1)/math.pi)*MM[j]
                results.append((d, gt[j], BASE[j]))
        pred = np.array([r[0] for r in results]); g = np.array([r[1] for r in results]); b = np.array([r[2] for r in results])
        print("\n===== 交叉验证结果（留出样本，n=%d）=====" % len(results))
        print("  自动掩膜   MAE = %.1f mm（中位 %.1f）" % (np.mean(abs(b-g)), np.median(abs(b-g))))
        print("  细化模型   MAE = %.1f mm（中位 %.1f）" % (np.mean(abs(pred-g)), np.median(abs(pred-g))))
        imp = 100*(1 - np.mean(abs(pred-g))/max(np.mean(abs(b-g)), 1e-9))
        print("  提升       %.1f%%" % imp)
        if imp <= 0:
            print("  提示：本次没有提升。可增加样本量，并确保训练集里同时包含"
                  "「需要修正的难例」和「本来就正确的正常图」。")

    if a.cv_only:
        print("\n(--cv-only：跳过最终模型训练)")
        return
    print("\n在全量数据上训练最终模型…")
    net = train_model(list(range(len(files))), max(a.epochs, 30))
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    torch.save(net.state_dict(), str(outp))
    print(f"模型已保存：{outp}   用时 {time.time()-t0:.0f}s")
    print("把它复制到程序的 models/refine_unet.pt 即可生效（无需改代码）。")


if __name__ == "__main__":
    main()
