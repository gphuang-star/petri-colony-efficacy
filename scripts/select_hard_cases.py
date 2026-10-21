# -*- coding: utf-8 -*-
"""按难度分层挑选值得人工标注的图片（给边界细化模型积累训练数据）。

思路：
  · 容易：径向纹理与 SAM 直径一致（比值 0.85~1.20）且无复核标记 -> 不标
  · 中等：SAM 少圈 1.2~3 倍，或有复核标记 -> **优先标**（这是最有效的训练样本）
  · 极端：SAM 只圈到菌饼（比值 > 3）或直径 < 10mm -> 限量标（占比过大会把模型带偏）

用法：
    python scripts/select_hard_cases.py ^
        --src-run D:\\out\\sam ^
        --out-dir D:\\out\\selection ^
        --limit 40 --max-extreme 0.20 --html

输出：
    selection.csv    每张图的比值、分类、原因
    review.html      只包含被挑中的图（可直接用「两点测距」标注）
"""
from __future__ import annotations
import argparse, csv, shutil, subprocess, sys
from pathlib import Path


def num(v):
    try:
        x = float(v)
        return x if x == x else None
    except Exception:
        return None


def classify(auto_mm, radial_mm, needs):
    if not auto_mm or auto_mm <= 0:
        return "unknown", "无自动直径"
    if not radial_mm or radial_mm <= 0:
        return "medium", "径向估计失败，需人工确认"
    ratio = radial_mm / auto_mm
    if ratio > 3.0 or auto_mm < 10.0:
        return "extreme", "疑似只圈到菌饼（比值 %.2f）" % ratio
    if ratio >= 1.20:
        return "medium", "SAM 少圈（比值 %.2f）" % ratio
    if ratio < 0.85:
        return "medium", "SAM 偏大（比值 %.2f）" % ratio
    if needs:
        return "medium", "有复核标记"
    return "easy", "与径向估计一致（比值 %.2f）" % ratio


def find_qa(qa_dir, key):
    for name in (f"{key}_qa.jpg", f"{key}_annotated.jpg", f"{key}.jpg"):
        p = qa_dir / name
        if p.exists():
            return p
    return None


def main():
    ap = argparse.ArgumentParser(description="按难度分层挑图")
    ap.add_argument("--src-run", required=True, help="建议用 run_boundary_v8 的输出目录（含 radial_mm / needs_manual）")
    ap.add_argument("--out-dir", required=True, help="输出目录")
    ap.add_argument("--limit", type=int, default=40, help="中等难例最多取多少张")
    ap.add_argument("--max-extreme", type=float, default=0.20, help="极端样本占比上限")
    ap.add_argument("--html", action="store_true", help="同时生成 review.html")
    a = ap.parse_args()

    src = Path(a.src_run)
    out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(open(src / "measurements_auto.csv", encoding="utf-8-sig", newline="")))
    qa = src / "qa"
    if not rows:
        print("measurements_auto.csv 为空"); return

    recs = []
    for r in rows:
        key = (r.get("qa_key") or Path(r.get("image_path", "")).stem)
        auto = num(r.get("equivalent_diameter_mm"))
        radial = num(r.get("radial_mm"))
        if radial is None:                       # v8 之外的结果只有比例
            rr = num(r.get("radial_ratio"))
            if rr and auto:
                radial = rr * auto
        needs = str(r.get("needs_manual") or "").strip() in ("1", "true", "True", "yes")
        cat, why = classify(auto, radial, needs)
        recs.append(dict(key=key, auto=auto, radial=radial,
                         ratio=(radial / auto) if (auto and radial) else None,
                         cat=cat, why=why, row=r))

    med = [x for x in recs if x["cat"] == "medium"]
    ext = [x for x in recs if x["cat"] == "extreme"]
    unk = [x for x in recs if x["cat"] == "unknown"]
    easy = [x for x in recs if x["cat"] == "easy"]

    med.sort(key=lambda x: -(x["ratio"] or 0))          # 少圈最多的先标
    max_ext = int(round(a.max_extreme * (min(len(med), a.limit) + len(ext))))
    picked = med[:a.limit] + ext[:max(0, max_ext)] + unk
    picked_keys = {x["key"] for x in picked}

    with open(out / "selection.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["key", "auto_mm", "radial_mm", "ratio", "category", "reason", "selected"])
        for x in recs:
            w.writerow([x["key"],
                        "" if x["auto"] is None else round(x["auto"], 2),
                        "" if x["radial"] is None else round(x["radial"], 2),
                        "" if x["ratio"] is None else round(x["ratio"], 3),
                        x["cat"], x["why"], "1" if x["key"] in picked_keys else "0"])

    print("总图数 %d" % len(recs))
    print("  容易   %3d  -> 不标" % len(easy))
    print("  中等   %3d  -> 取 %d 张标" % (len(med), min(len(med), a.limit)))
    print("  极端   %3d  -> 取 %d 张（占比上限 %.0f%%）" % (len(ext), max(0, max_ext), a.max_extreme * 100))
    print("  未知   %3d  -> 全部放入" % len(unk))
    print("总计挑出 %d 张 -> %s" % (len(picked), out / "selection.csv"))

    if a.html and picked:
        work = out / "_review_input"
        if work.exists():
            shutil.rmtree(work)
        (work / "qa").mkdir(parents=True)
        keep = {x["key"] for x in picked}
        with open(work / "measurements_auto.csv", "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            for r in rows:
                key = (r.get("qa_key") or Path(r.get("image_path", "")).stem)
                if key in keep:
                    w.writerow(r)
        with open(work / "mapping.csv", "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh); w.writerow(["qa_key", "filename", "slide", "topic", "treat"])
            for i, x in enumerate(picked, 1):
                w.writerow([x["key"], x["key"] + ".jpg", i, "难例", x["cat"]])
        n = 0
        for x in picked:
            src_img = find_qa(qa, x["key"])
            if src_img:
                shutil.copy2(src_img, work / "qa" / (x["key"] + "_qa.jpg")); n += 1
        print("已复制 QA 图 %d 张" % n)
        script = Path(__file__).resolve().parent / "annot_draw_gallery.py"
        cmd = [sys.executable, str(script), "--res-dir", str(work),
               "--mapping", str(work / "mapping.csv"),
               "--out-html", str(out / "review.html"),
               "--title", "难例复核（按难度挑选）"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        print(r.stdout.strip())
        if r.returncode != 0:
            print("生成 review.html 失败:", r.stderr.strip()[-500:])


if __name__ == "__main__":
    main()