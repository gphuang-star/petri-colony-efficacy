# -*- coding: utf-8 -*-
"""检查边界细化数据集（合并多个数据集之前先跑一遍）。

真正"坏"的样本只有两类：
  · base/sam 为空（自动掩膜完全没东西）
  · man 为空（人工掩膜没画上）
其余都是统计信息。base/man 比值很大通常是"SAM 只圈到菌饼、人工是满皿"这类难例，
是**有价值的训练样本**，不要因为比值大就删掉。

用法：
    python scripts/check_dataset.py --dataset D:\\dataset
"""
from __future__ import annotations
import argparse
from pathlib import Path

import numpy as np


def main():
    ap = argparse.ArgumentParser(description="检查细化数据集")
    ap.add_argument("--dataset", required=True, help="数据集目录（递归读取 *.npz）")
    ap.add_argument("--min-px", type=int, default=50, help="掩膜最小像素数，低于此视为空")
    a = ap.parse_args()

    files = sorted(Path(a.dataset).rglob("*.npz"))
    if not files:
        print("没有找到 npz 文件:", a.dataset)
        return

    rows, broken = [], []
    for f in files:
        try:
            z = np.load(f, allow_pickle=True)
            base = z["base"] if "base" in z.files else (z["sam"] if "sam" in z.files else None)
            if base is None or "man" not in z.files:
                broken.append((f, "缺少 base/sam 或 man")); continue
            a0 = int((base > 0).sum())
            a1 = int((z["man"] > 0).sum())
            if a0 < a.min_px:
                broken.append((f, "自动掩膜为空 (base=%d)" % a0)); continue
            if a1 < a.min_px:
                broken.append((f, "人工掩膜为空 (man=%d)" % a1)); continue
            rows.append((f, a0, a1, a1 / max(a0, 1)))
        except Exception as e:
            broken.append((f, "读取失败: %s" % e))

    print("样本总数: %d   可用: %d   异常: %d" % (len(files), len(rows), len(broken)))

    if rows:
        r = np.array([x[3] for x in rows], dtype=float)
        print("base/man 面积比: 中位 %.2f | P10 %.2f | P90 %.2f | 最大 %.2f" % (
            float(np.median(r)), float(np.percentile(r, 10)),
            float(np.percentile(r, 90)), float(r.max())))
        n_big = int((r > 10).sum())
        print("极端放大样本（比值>10）: %d 张 —— 通常是'只圈到菌饼'难例，可保留" % n_big)
        top = sorted(rows, key=lambda x: -x[3])[:5]
        for f, a0, a1, rr in top:
            print("   %-40s base=%6d man=%7d 比值=%.1f" % (f.name, a0, a1, rr))

    if broken:
        print("\n[!] 异常样本 %d 个（这些不要用于训练）:" % len(broken))
        for f, why in broken[:30]:
            print("   %-40s %s" % (f.name, why))
        if len(broken) > 30:
            print("   ... 还有 %d 个" % (len(broken) - 30))
    else:
        print("\n[OK] 没有空掩膜样本。")


if __name__ == "__main__":
    main()