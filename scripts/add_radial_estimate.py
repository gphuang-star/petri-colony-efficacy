# -*- coding: utf-8 -*-
"""给已有的测量结果补算"径向纹理估计"（radial_mm / radial_ratio）。

用途：有些批次的 CSV 只有 SAM/细化结果，没有径向估计，导致无法做难度分层。
本脚本复用已有结果，只补算径向这一列，**不需要重跑 SAM**。

用法：
    python scripts/add_radial_estimate.py --src-run D:\\out\\v9 --out-dir D:\\out\\v9_radial
"""
from __future__ import annotations
import argparse, csv, shutil, sys
from pathlib import Path

import numpy as np

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
import cv2
from measure_colony_image import imread_unicode, detect_dish
from radial_texture_engine import radial_boundary, centroid_cross


def main():
    ap = argparse.ArgumentParser(description="补算径向纹理估计")
    ap.add_argument("--src-run", required=True, help="含 measurements_auto.csv 与 qa/")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--dish-mm", type=float, default=86.0)
    ap.add_argument("--work-dim", type=int, default=1400)
    a = ap.parse_args()

    src = Path(a.src_run); out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(open(src / "measurements_auto.csv", encoding="utf-8-sig", newline="")))
    if not rows:
        print("CSV 为空"); return

    ok = fail = 0
    for i, r in enumerate(rows, 1):
        r["radial_mm"] = r.get("radial_mm", "")
        p = Path(r.get("image_path", ""))
        radial_mm = None
        try:
            if p.exists():
                img = imread_unicode(p)
                h, w = img.shape[:2]
                if max(h, w) > a.work_dim:
                    s = a.work_dim / max(h, w)
                    img = cv2.resize(img, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_AREA)
                cx, cy, rad, _, _, _ = detect_dish(img)
                if rad > 0:
                    rb = radial_boundary(img, cx, cy, rad)
                    if rb is not None:
                        cc = centroid_cross(rb["mask"], a.dish_mm / (2.0 * rad))
                        if cc:
                            radial_mm = (cc["m1"] + cc["m2"]) / 2.0
        except Exception:
            radial_mm = None

        auto = None
        try:
            auto = float(r.get("equivalent_diameter_mm") or "")
        except Exception:
            auto = None
        r["radial_mm"] = "" if radial_mm is None else round(radial_mm, 4)
        r["radial_ratio"] = "" if not (auto and radial_mm) else round(radial_mm / auto, 4)
        if auto and radial_mm:
            ratio = radial_mm / auto
            r["needs_manual"] = "" if 0.80 <= ratio <= 1.25 else "1"
            ok += 1
        else:
            r["needs_manual"] = "1"
            fail += 1
        if i % 25 == 0:
            print("  %d/%d" % (i, len(rows)), flush=True)

    fields = list(rows[0].keys())
    with open(out / "measurements_auto.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

    qa_src = src / "qa"; qa_dst = out / "qa"
    if qa_src.is_dir():
        if qa_dst.exists():
            shutil.rmtree(qa_dst)
        shutil.copytree(qa_src, qa_dst)

    print("完成：%d 行，径向成功 %d，失败 %d" % (len(rows), ok, fail))
    print("输出：", out / "measurements_auto.csv")


if __name__ == "__main__":
    main()