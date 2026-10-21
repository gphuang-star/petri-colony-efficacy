# -*- coding: utf-8 -*-
"""v8 = v6 (trust SAM + circle fit) with the two fixes the user asked for:

Fix 1  no more silent "suppressed -> 6 mm".  A low-contrast / unreliable mask is
       now only *flagged* (needs_manual) with a reason; the value is left as the
       best available estimate and both candidates are exported.
Fix 2  leak/under-segmentation flag: if the radial texture estimate is much
       larger than the automatic one (ratio > 1.8, or > 1.35 with a ragged mask)
       the image is flagged for manual boundary drawing.
"""
from __future__ import annotations
import argparse, csv, math, sys
from pathlib import Path
# --- 必须在 import numpy/cv2 之前把 samlibs 挂上（含 numpy 1.26），
#     否则会加载到 numpy 2.x，torch/SAM 报 "Could not infer dtype" ---
# 先解析 --sam-libs / --sam-libs=，必须在 import numpy 之前
for _i, _a in enumerate(sys.argv):
    _v = None
    if _a == "--sam-libs" and _i + 1 < len(sys.argv):
        _v = sys.argv[_i + 1]
    elif _a.startswith("--sam-libs="):
        _v = _a.split("=", 1)[1]
    if _v and _v not in sys.path:
        sys.path.insert(0, _v)

_HERE = Path(__file__).resolve().parent
for _cand in (_HERE / "samlibs", _HERE.parent / "samlibs"):
    if _cand.is_dir():
        if str(_cand) not in sys.path:
            sys.path.insert(0, str(_cand))
        break

import cv2, numpy as np
_here = str(Path(__file__).resolve().parent); sys.path.insert(0, _here)
from measure_colony_image import imread_unicode, detect_dish, detect_plug, imwrite_unicode
from radial_texture_engine import radial_boundary, centroid_cross

def read_gray(p):
    d = np.fromfile(str(p), dtype=np.uint8); return cv2.imdecode(d, cv2.IMREAD_GRAYSCALE)

def fill_holes(m):
    ff = m.copy(); h, w = ff.shape[:2]; pad = np.zeros((h+2, w+2), np.uint8)
    cv2.floodFill(ff, pad, (0, 0), 255); return cv2.bitwise_or(m, cv2.bitwise_not(ff))

def circularity(m):
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts: return 0.0
    c = max(cnts, key=cv2.contourArea); a = cv2.contourArea(c); p = cv2.arcLength(c, True)
    return float(4*math.pi*a/(p*p)) if p > 0 else 0.0

def fit_circle(m):
    M = cv2.moments(m, binaryImage=True)
    if M["m00"] <= 0: return None
    cx, cy = M["m10"]/M["m00"], M["m01"]/M["m00"]
    rad = math.sqrt(np.count_nonzero(m)/math.pi)
    out = np.zeros(m.shape[:2], np.uint8); cv2.circle(out, (int(round(cx)), int(round(cy))), int(round(rad)), 255, -1)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-run", required=True); ap.add_argument("--out-dir", required=True)
    ap.add_argument("--work-dim", type=int, default=1400)
    ap.add_argument("--circ", type=float, default=1.01, help="圆拟合阈值；默认 1.01 表示关闭（不把轮廓替换成标准圆）")
    ap.add_argument("--circle-fit", action="store_true", help="显式启用圆拟合（旧行为，会把菌落画成标准圆）")
    ap.add_argument("--plug-mm", type=float, default=6.0)
    ap.add_argument("--supp-contrast", type=float, default=40.0)
    ap.add_argument("--leak-hi", type=float, default=1.80)
    ap.add_argument("--leak-mid", type=float, default=1.35)
    ap.add_argument("--solid-mid", type=float, default=0.75)
    a = ap.parse_args()
    run = Path(a.src_run); out = Path(a.out_dir); qa_out = out/"qa"; qa_out.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(open(run/"measurements_auto.csv", encoding="utf-8-sig", newline="")))
    out_rows = []; n_flag = 0; n_leak = 0; n_circ = 0; n_supp = 0
    for r in rows:
        p = Path(r["image_path"]); key = r.get("qa_key") or p.stem
        img = imread_unicode(p); h0, w0 = img.shape[:2]
        if a.work_dim and max(h0, w0) > a.work_dim:
            s = a.work_dim/max(h0, w0); img = cv2.resize(img, (int(round(w0*s)), int(round(h0*s))), interpolation=cv2.INTER_AREA)
        nr = dict(r); nr["boundary_engine"] = "v8"
        reasons = []
        sam = read_gray(run/"qa"/f"{key}_mask.png")
        if sam is not None and sam.shape[:2] != img.shape[:2]:
            sam = cv2.resize(sam, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
        cx, cy, rad, _, _, _ = detect_dish(img)
        if sam is None or rad <= 0:
            nr["boundary_method"] = "failed"; nr["needs_manual"] = "1"; nr["review_reason"] = "皿心/掩膜失败"
            out_rows.append(nr); imwrite_unicode(qa_out/f"{key}_qa.jpg", img); continue
        scale = 86/(2*rad)
        mask = (sam > 0).astype(np.uint8)*255
        area_raw = int(np.count_nonzero(mask)); filled = fill_holes(mask); area_fill = int(np.count_nonzero(filled))
        ring_like = area_raw > 0 and (area_fill/area_raw) > 2.5
        if not ring_like: mask = filled
        method = (r.get("boundary_method") or "sam_v2")
        # ---- radial candidate ----
        rb = radial_boundary(img, cx, cy, rad); radial_mm = ""
        if rb is not None:
            cc0 = centroid_cross(rb["mask"], scale)
            if cc0: radial_mm = (cc0["m1"]+cc0["m2"])/2
        auto_mm = 2*math.sqrt(max(area_raw,1)/math.pi)*scale
        ratio = (radial_mm/auto_mm) if (radial_mm and auto_mm > 0) else 0
        # ---- Fix 1: low contrast / unreliable mask -> flag, never force a value ----
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        hh, ww = g.shape[:2]; yy, xx = np.mgrid[0:hh, 0:ww]
        dd = np.sqrt((xx-cx)**2 + (yy-cy)**2)
        px, py, pr, _, pm = detect_plug(img, (cx, cy), rad, None)
        if pm == "dish_center_fallback" or not (2.0 < pr*scale < 6.0):
            px, py, pr = float(cx), float(cy), (a.plug_mm/2)/scale
        near = (dd >= 1.2*pr) & (dd <= min(3.0*pr, 0.35*rad)); agarr = (dd >= 0.70*rad) & (dd <= 0.88*rad)
        contrast = None
        if near.sum() >= 50 and agarr.sum() >= 50:
            contrast = abs(float(np.median(g[near])) - float(np.median(g[agarr])))
        unreliable = ring_like or any(k in method for k in ("bright_component", "rim", "suppressed", "failed"))
        if contrast is not None and contrast < a.supp_contrast and unreliable:
            reasons.append("对比度低+掩膜不可靠（疑无明显生长，请人工确认）"); n_supp += 1
        if ring_like: reasons.append("掩膜呈环状/空心")
        # ---- Fix 2: leak (under-segmentation) detection ----
        cnts, _ = cv2.findContours((sam > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        solidity = 0.0
        if cnts:
            allpts = np.vstack(cnts); ha = cv2.contourArea(cv2.convexHull(allpts)); solidity = area_raw/max(ha, 1)
        if ratio > a.leak_hi:
            reasons.append(f"疑漏圈（径向={radial_mm:.1f} vs 自动={auto_mm:.1f}，比值{ratio:.2f}）"); n_leak += 1
        elif ratio > a.leak_mid and solidity < a.solid_mid:
            reasons.append(f"可能漏圈（比值{ratio:.2f}，实心度{solidity:.2f}）"); n_leak += 1
        # ---- circle fit ----
        if a.circle_fit and not ring_like and circularity(mask) >= a.circ:
            fc = fit_circle(mask)
            if fc is not None: mask = fc; method += "+circle"; n_circ += 1
        cc = centroid_cross(mask, scale)
        if cc is None:
            nr["boundary_method"] = "failed"; nr["needs_manual"] = "1"; nr["review_reason"] = "量取失败"
            out_rows.append(nr); continue
        m1, m2 = cc["m1"], cc["m2"]
        nr["boundary_method"] = method
        nr["m1"] = f"{m1:.4f}"; nr["m2"] = f"{m2:.4f}"
        for k in range(3, 7): nr[f"m{k}"] = ""
        nr["equivalent_diameter_mm"] = f"{(m1+m2)/2:.4f}"
        nr["radial_mm"] = f"{radial_mm:.3f}" if radial_mm != "" else ""
        nr["radial_ratio"] = f"{ratio:.3f}" if ratio else ""
        nr["solidity"] = f"{solidity:.3f}"
        nr["gray_contrast"] = "" if contrast is None else f"{contrast:.1f}"
        nr["needs_manual"] = "1" if reasons else "0"
        nr["review_reason"] = "；".join(reasons)
        if reasons: n_flag += 1
        vis = img.copy()
        cont = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
        cv2.drawContours(vis, cont, -1, (0, 200, 0), 3)
        # cyan radial candidate is drawn ONLY on flagged images (as a hint when you
        # have to fix the boundary yourself); elsewhere it is just clutter
        if rb is not None and reasons:
            cv2.drawContours(vis, cv2.findContours(rb["mask"], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0], -1, (255, 255, 0), 2)
        y0 = int(round(cc["cy"])); x0 = int(round(cc["cx"]))
        cv2.line(vis, (cc["xl"], y0), (cc["xr"], y0), (0, 255, 255), 2)
        cv2.line(vis, (x0, cc["yt"]), (x0, cc["yb"]), (0, 255, 255), 2)
        txt = f"auto={(m1+m2)/2:.2f}mm" + (f"  radial={radial_mm:.2f}mm" if radial_mm != "" else "")
        cv2.rectangle(vis, (4, 4), (20+13*len(txt), 40), (0, 0, 0), -1)
        cv2.putText(vis, txt, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
        if reasons:
            cv2.putText(vis, "NEEDS REVIEW", (10, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2, cv2.LINE_AA)
        imwrite_unicode(qa_out/f"{key}_qa.jpg", vis)
        out_rows.append(nr)
    with (out/"measurements_auto.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()), extrasaction="ignore")
        w.writeheader(); w.writerows(out_rows)
    print(f"v8: {len(out_rows)} imgs | circle-fit {n_circ} | flagged {n_flag} (leak {n_leak}, low-contrast {n_supp})")

if __name__ == "__main__": main()
