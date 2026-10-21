# -*- coding: utf-8 -*-
"""从「原图 + 自动掩膜 + 人工标注」构建边界细化模型的训练集。

输入
  --annotations   人工标注页导出的 CSV（含 图片名 / 形状JSON / 人工直径mm）
  --images        原图文件夹（递归查找同名图片）
  --base-mask-dir （可选）自动测量输出的掩膜目录（文件名 <stem>.png）
输出
  <out-dir>/*.npz     每个样本: img(512,512,3) base(512,512) man(512,512) mmpp
  <out-dir>/index.csv 汇总（含 人工/自动 直径，便于核对）

用法示例
  python scripts/build_mask_dataset.py \
      --annotations 人工框取.csv \
      --images D:\\photos \
      --base-mask-dir D:\\photos\\_菌落测量输出\\auto_masks \
      --out-dir D:\\dataset
"""
from __future__ import annotations
import argparse, csv, json, math, sys
from pathlib import Path
import cv2
import numpy as np

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
try:
    from measure_colony_image import imread_unicode, detect_dish
except Exception:                                    # 允许单独放置
    imread_unicode = None
    detect_dish = None

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
SIZE, R_TARGET, WORK_DIM = 512, 225.0, 1400


# ---------------------------------------------------------------- 形状 -> 掩膜
def shape_to_mask(shape, W, H, out_w, out_h, x0, y0, scale):
    """把标注形状(归一化坐标)画到裁切后的画布上"""
    mask = np.zeros((out_h, out_w), np.uint8)

    def to_xy(p):
        ox, oy = float(p[0]) * W, float(p[1]) * H
        return int(round((ox - x0) * scale)), int(round((oy - y0) * scale))

    t = shape.get("type")
    if t == "circle":
        cx, cy = to_xy((shape["cx"], shape["cy"]))
        r = max(1, int(round(float(shape["r"]) * W * scale)))
        cv2.circle(mask, (cx, cy), r, 255, -1)
    elif t == "poly":
        pts = np.array([to_xy(p) for p in shape.get("pts", [])], np.int32)
        if len(pts) >= 3:
            cv2.fillPoly(mask, [pts], 255)
    elif t == "brush":
        for st in shape.get("strokes", []):
            pts = [to_xy(p) for p in st.get("pts", [])]
            width = max(2, int(round(float(st.get("r", 0.01)) * W * scale * 2)))
            if not pts:
                continue
            if len(pts) == 1:
                cv2.circle(mask, pts[0], width // 2, 0 if st.get("erase") else 255, -1)
            else:
                cv2.polylines(mask, [np.array(pts, np.int32)], False,
                              0 if st.get("erase") else 255, width, cv2.LINE_AA)
    return mask


def find_image(images_dir, stem, filename=""):
    """优先按「原图文件」精确匹配，其次按 stem，最后去掉序号前缀再试（024_24 -> 24）"""
    if filename:
        for p in images_dir.rglob("*"):
            if p.is_file() and p.name == filename:
                return p
    cands = [p for p in images_dir.rglob("*") if p.is_file() and p.suffix.lower() in IMG_EXT]
    for p in cands:
        if p.stem == stem:
            return p
    tail = stem.split("_", 1)[-1]
    if tail and tail != stem:
        for p in cands:
            if p.stem == tail:
                return p
    return None


def main():
    ap = argparse.ArgumentParser(description="构建边界细化模型训练集")
    ap.add_argument("--annotations", required=True, help="标注页导出的 CSV")
    ap.add_argument("--images", required=True, help="原图文件夹（递归查找）")
    ap.add_argument("--out-dir", required=True, help="输出数据集目录")
    ap.add_argument("--base-mask-dir", default="", help="自动掩膜目录（可选，文件名 <图片名>.png）")
    ap.add_argument("--size", type=int, default=SIZE, help="裁切输出边长，默认 512")
    ap.add_argument("--dish-mm", type=float, default=86.0, help="培养皿直径 mm")
    ap.add_argument("--work-dim", type=int, default=WORK_DIM, help="处理分辨率上限")
    ap.add_argument("--measurements", default="",
                    help="程序输出的 measurements.csv；默认自动查找 <images>/菌落测量输出/measurements.csv")
    ap.add_argument("--auto-identity", type=int, default=1,
                    help="1=自动把「状态=自动」的图作为保持样本加入训练集（默认开）")
    ap.add_argument("--identity-ratio", type=float, default=1.0,
                    help="保持样本数量上限 = 人工标注数 × 该系数，默认 1.0")
    ap.add_argument("--max-base-man-ratio", type=float, default=8.0,
                    help="两点测距样本中，基础掩膜面积/人工目标面积超过此值时跳过（疑似整皿/皿边），请改用涂抹/多边形")
    ap.add_argument("--auto-status", default="自动", help="measurements.csv 中视为\"已正确\"的状态值")
    a = ap.parse_args()

    images_dir = Path(a.images); out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    base_dir = Path(a.base_mask_dir) if a.base_mask_dir else None
    rows = list(csv.DictReader(open(a.annotations, encoding="utf-8-sig", newline="")))
    annotated = set()

    ok, skipped = 0, []
    index = []
    for r in rows:
        stem = (r.get("图片名") or r.get("image") or r.get("stem") or "").strip()
        shape_json = (r.get("形状JSON") or r.get("shape") or "").strip()
        if not stem or not shape_json:
            continue
        try:
            shape = json.loads(shape_json)
        except Exception:
            skipped.append((stem, "形状JSON 解析失败")); continue
        if not shape:
            continue
        fname = (r.get("原图文件") or r.get("file") or "").strip()
        p = find_image(images_dir, stem, fname)
        if p is None:
            skipped.append((stem, "找不到原图")); continue

        img = imread_unicode(p) if imread_unicode else cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)
        H, W = img.shape[:2]
        work = img
        if max(H, W) > a.work_dim:
            s = a.work_dim / max(H, W)
            work = cv2.resize(img, (int(round(W*s)), int(round(H*s))), interpolation=cv2.INTER_AREA)
        cx, cy, rad, _, _, _ = detect_dish(work)
        if rad <= 0:
            skipped.append((stem, "未检测到培养皿")); continue
        sc = a.work_dim / max(H, W) if max(H, W) > a.work_dim else 1.0
        cx_o, cy_o, rad_o = cx/sc, cy/sc, rad/sc          # 换算回原图坐标

        k = R_TARGET / rad_o
        cs = int(round(a.size / k))
        x0, y0 = int(round(cx_o - cs/2)), int(round(cy_o - cs/2))

        def crop(im, interp):
            shp = (cs, cs) if im.ndim == 2 else (cs, cs, im.shape[2])
            c = np.zeros(shp, im.dtype)
            sx0, sy0 = max(0, x0), max(0, y0)
            sx1, sy1 = min(im.shape[1], x0+cs), min(im.shape[0], y0+cs)
            c[sy0-y0:sy1-y0, sx0-x0:sx1-x0] = im[sy0:sy1, sx0:sx1]
            return cv2.resize(c, (a.size, a.size), interpolation=interp)

        cimg = crop(img, cv2.INTER_AREA)
        man = shape_to_mask(shape, W, H, a.size, a.size, x0, y0, a.size/cs)
        if base_dir:
            bf = base_dir / f"{stem}.png"
            if not bf.exists():                       # 原图文件名（去序号前缀）
                tail = stem.split("_", 1)[-1]
                for cand_name in ([Path(fname).stem] if fname else []) + [tail, stem]:
                    for suffix in ("", "_mask"):
                        c2 = base_dir / f"{cand_name}{suffix}.png"
                        if cand_name and c2.exists():
                            bf = c2; break
                    if bf.exists():
                        break
            if not bf.exists():
                # 只在文件名以 "<tail>_" 开头时匹配，避免 2 命中 12/22/32
                tail = stem.split("_", 1)[-1]
                for c in sorted(base_dir.glob(f"{tail}_*.png")):
                    bf = c; break
            if bf and Path(bf).exists():
                bm = cv2.imdecode(np.fromfile(str(bf), np.uint8), cv2.IMREAD_GRAYSCALE)
                base = crop(bm, cv2.INTER_NEAREST)
            else:
                base = np.zeros((a.size, a.size), np.uint8)
        else:
            base = np.zeros((a.size, a.size), np.uint8)

        # ---- 两点测距标注：按测得直径等比例缩放原掩膜（保留不规则形状，不画成圆）----
        if str(shape.get("type")) == "line":
            lns = shape.get("lines") or []
            if lns and int((base > 0).sum()) > 0:
                vals = []
                for ln in lns:
                    dx = (float(ln[1][0]) - float(ln[0][0])) * W
                    dy = (float(ln[1][1]) - float(ln[0][1])) * H
                    vals.append(math.hypot(dx, dy))
                vals = [v for v in vals if v > 0]
                if vals:
                    mmpp_o = a.dish_mm / (2.0 * rad_o)
                    target_mm = mmpp_o * (sum(vals) / len(vals))
                    mmpp_c = a.dish_mm / (2.0 * R_TARGET)
                    target_area_px = math.pi * ((target_mm / 2.0) / mmpp_c) ** 2
                    base_area = int((base > 0).sum())
                    if base_area > 0 and target_area_px > 0 and base_area / target_area_px > a.max_base_man_ratio:
                        skipped.append((stem, "两点测距但基础掩膜过大（base/target=%.1f），请改用涂抹/多边形" % (base_area / target_area_px)))
                        continue
                    d0_mm = 2 * math.sqrt(max(int((base > 0).sum()), 1) / math.pi) * mmpp_c
                    if d0_mm > 1e-6:
                        k2 = max(0.2, min(5.0, target_mm / d0_mm))
                        M0 = cv2.moments((base > 0).astype(np.uint8), binaryImage=True)
                        if M0["m00"] > 0:
                            bcx, bcy = M0["m10"] / M0["m00"], M0["m01"] / M0["m00"]
                            # 以两条测距线的中点为人工目标中心：先缩放，再把掩膜质心平移过去。
                            # 这对“绿圈完全圈到皿边/皿盖、位置也错了”的样本很关键。
                            mids = [((float(ln[0][0]) + float(ln[1][0])) * 0.5 * W,
                                     (float(ln[0][1]) + float(ln[1][1])) * 0.5 * H) for ln in lns]
                            tx = sum(p[0] for p in mids) / len(mids)
                            ty = sum(p[1] for p in mids) / len(mids)
                            tcx = (tx - x0) * (a.size / cs)
                            tcy = (ty - y0) * (a.size / cs)
                            M2 = np.float32([[k2, 0, tcx - k2 * bcx],
                                             [0, k2, tcy - k2 * bcy]])
                            man = cv2.warpAffine((base > 0).astype(np.uint8) * 255, M2,
                                                 (a.size, a.size), flags=cv2.INTER_NEAREST)

        mmpp = a.dish_mm / (2.0 * R_TARGET)
        np.savez_compressed(out / f"{stem}.npz",
                            img=cimg, base=(base > 0).astype(np.uint8),
                            man=(man > 0).astype(np.uint8), mmpp=mmpp)
        d = lambda m: 2*math.sqrt(max(int((m > 0).sum()), 1)/math.pi) * mmpp
        index.append(dict(key=stem, image=str(p), source="manual",
                          base_mm=round(d(base), 2), manual_mm=round(d(man), 2), mmpp=round(mmpp, 5)))
        ok += 1; annotated.add(stem)

    # ---------- 自动保持样本：状态=自动 的图，用自动掩膜当标注 ----------
    keep_n = 0
    if a.auto_identity:
        meas_path = Path(a.measurements) if a.measurements else (images_dir / "菌落测量输出" / "measurements.csv")
        if meas_path.exists():
            try:
                mrows = list(csv.DictReader(open(meas_path, encoding="utf-8-sig", newline="")))
            except Exception:
                mrows = []
            cands = []
            for r in mrows:
                st = (r.get("状态") or "").strip()
                stem = Path((r.get("原图") or "").strip()).stem
                if not stem or st != a.auto_status or stem in annotated:
                    continue
                mask_rel = (r.get("自动掩膜") or "").strip()
                bf = (meas_path.parent / mask_rel) if mask_rel else (meas_path.parent / "auto_masks" / f"{stem}.png")
                p = find_image(images_dir, stem, (r.get("原图") or "").strip())
                if p is not None and Path(bf).exists():
                    cands.append((stem, p, Path(bf)))
            limit = int(max(0, round(len(index) * a.identity_ratio)))
            for stem, p, bf in cands[:limit]:
                img = imread_unicode(p) if imread_unicode else cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)
                H, W = img.shape[:2]
                work = img
                if max(H, W) > a.work_dim:
                    sc0 = a.work_dim / max(H, W)
                    work = cv2.resize(img, (int(round(W*sc0)), int(round(H*sc0))), interpolation=cv2.INTER_AREA)
                cx, cy, rad, _, _, _ = detect_dish(work)
                if rad <= 0:
                    continue
                sc = a.work_dim / max(H, W) if max(H, W) > a.work_dim else 1.0
                cx_o, cy_o, rad_o = cx/sc, cy/sc, rad/sc
                k = R_TARGET / rad_o
                cs = int(round(a.size / k))
                x0, y0 = int(round(cx_o - cs/2)), int(round(cy_o - cs/2))
                def crop2(im, interp):
                    shp = (cs, cs) if im.ndim == 2 else (cs, cs, im.shape[2])
                    c = np.zeros(shp, im.dtype)
                    sx0, sy0 = max(0, x0), max(0, y0)
                    sx1, sy1 = min(im.shape[1], x0+cs), min(im.shape[0], y0+cs)
                    c[sy0-y0:sy1-y0, sx0-x0:sx1-x0] = im[sy0:sy1, sx0:sx1]
                    return cv2.resize(c, (a.size, a.size), interpolation=interp)
                cimg = crop2(img, cv2.INTER_AREA)
                bm = cv2.imdecode(np.fromfile(str(bf), np.uint8), cv2.IMREAD_GRAYSCALE)
                if bm is None:
                    continue
                cmask = crop2(bm, cv2.INTER_NEAREST)
                if int((cmask > 0).sum()) < 50:
                    continue
                mmpp = a.dish_mm / (2.0 * R_TARGET)
                np.savez_compressed(out / f"{stem}.npz", img=cimg,
                                    base=(cmask > 0).astype(np.uint8),
                                    man=(cmask > 0).astype(np.uint8), mmpp=mmpp)
                dd = 2*math.sqrt(max(int((cmask > 0).sum()), 1)/math.pi) * mmpp
                index.append(dict(key=stem, image=str(p), source="auto_identity",
                                  base_mm=round(dd, 2), manual_mm=round(dd, 2), mmpp=round(mmpp, 5)))
                keep_n += 1
        else:
            print(f"提示：未找到 {meas_path}，跳过自动保持样本（可用 --measurements 指定）")

    with (out/"index.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["key", "image", "source", "base_mm", "manual_mm", "mmpp"])
        w.writeheader(); w.writerows(index)
    n_manual = sum(1 for r in index if r.get("source") == "manual")
    print(f"完成：人工标注样本 {n_manual} 个 + 自动保持样本 {keep_n} 个 -> {out}")
    if skipped:
        print(f"跳过 {len(skipped)} 条：")
        for s, why in skipped[:10]:
            print(f"  - {s}: {why}")
    print("下一步训练：")
    print(f'  python scripts/train_mask_refiner.py --dataset "{out}" --out "{out}/../refine_unet.pt"')


if __name__ == "__main__":
    main()
