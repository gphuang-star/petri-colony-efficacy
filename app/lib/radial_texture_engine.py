# -*- coding: utf-8 -*-
"""Radial texture-profile colony boundary.

Idea: mycelium (even sparse 'hairy' hyphae) raises the local texture (std) above
the smooth agar.  Sampling the texture along rays from the plate centre gives a
radial profile whose outer drop marks the colony edge.  Taking the outermost
radius that is still colony-like, per angle, then smoothing, yields a smooth
closed boundary that also captures hairy / near-full colonies.
"""
from __future__ import annotations
import math
import cv2, numpy as np

def local_texture(gray, k):
    k = int(k) | 1
    mean = cv2.boxFilter(gray, -1, (k, k))
    sq = cv2.boxFilter(gray * gray, -1, (k, k))
    return np.sqrt(np.clip(sq - mean * mean, 0, None))

def circular_smooth(v, win):
    n = len(v); win = int(win)|1
    if win < 3: return v
    ext = np.concatenate([v[-win:], v, v[:win]])
    k = np.ones(win)/win
    s = np.convolve(ext, k, mode='same')
    return s[win:win+n]

def radial_boundary(image, cx, cy, r, frac=0.50, n_ang=180, step=1.0, smooth_win=15):
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    k = max(9, int(r * 0.06) | 1)
    tex = local_texture(gray, k)
    tex = cv2.GaussianBlur(tex, (0, 0), max(1.0, r * 0.01))
    rmax = 0.965 * r
    rs = np.arange(0, rmax, step)
    if len(rs) < 8:
        return None
    angs = np.linspace(0, 2 * math.pi, n_ang, endpoint=False)
    cos = np.cos(angs)[:, None]; sin = np.sin(angs)[:, None]
    xs = np.clip(np.round(cx + cos * rs[None, :]).astype(int), 0, w - 1)
    ys = np.clip(np.round(cy + sin * rs[None, :]).astype(int), 0, h - 1)
    prof = tex[ys, xs]                      # (n_ang, len(rs))
    # radial smoothing ~1.5 mm
    px_per_mm = (2 * r) / 86.0
    win = max(3, int(round(1.5 * px_per_mm)) | 1)
    ker = np.ones(win) / win
    prof_s = np.apply_along_axis(lambda v: np.convolve(v, ker, mode="same"), 1, prof)
    inner = max(3, int(0.30 * len(rs)))
    plateau = float(np.median(prof_s[:, :inner]))
    # agar reference from a band that excludes the bright plastic rim
    lo, hi = int(0.72 * len(rs)), int(0.90 * len(rs))
    outer = prof_s[:, lo:hi]
    agar = float(np.median(outer)) if outer.size else 0.0
    if plateau <= max(agar * 1.25, 1.0):
        return None
    thr = agar + frac * (plateau - agar)
    # global profile -> robust base radius
    gp = np.median(prof_s, axis=0)
    need = max(3, int(0.08 * len(rs)))
    run = 0; base = None
    for j in range(len(rs)):
        if gp[j] < thr:
            run += 1
            if run >= need:
                base = rs[max(0, j - run + 1)]
                break
        else:
            run = 0
    if base is None or base < 0.05 * r:
        base = rmax
    lo = int(np.searchsorted(rs, max(0.0, 0.60 * base)))
    hi = int(min(len(rs) - 1, np.searchsorted(rs, min(rmax, 1.40 * base))))
    rad = np.full(n_ang, base, np.float32)
    if hi > lo:
        for i in range(n_ang):
            seg = prof_s[i, lo:hi + 1]
            idx = np.where(seg >= thr)[0]
            if len(idx):
                rad[i] = rs[lo + idx[-1]]
    rad = circular_smooth(rad, smooth_win)
    # convert to polygon
    pts = np.stack([cx + rad * np.cos(angs), cy + rad * np.sin(angs)], 1).astype(np.int32)
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [pts], 255)
    M = cv2.moments(mask, binaryImage=True)
    if M["m00"] <= 0: return None
    ccx, ccy = M["m10"] / M["m00"], M["m01"] / M["m00"]
    return dict(mask=mask, pts=pts, rad=rad, cx=ccx, cy=ccy, angs=angs,
                plateau=plateau, agar=agar, thr=thr, mean_radius=float(rad.mean()))

def centroid_cross(mask, scale):
    occ = (mask > 0).astype(np.uint8)
    M = cv2.moments(occ, binaryImage=True)
    if M["m00"] <= 0: return None
    cx, cy = M["m10"] / M["m00"], M["m01"] / M["m00"]
    def run(binrow, x):
        n = len(binrow); x = int(min(max(x, 0), n - 1))
        if not binrow[x]:
            for d in range(1, n):
                if x - d >= 0 and binrow[x - d]: x = x - d; break
                if x + d < n and binrow[x + d]: x = x + d; break
            else: return None
        l = x
        while l - 1 >= 0 and binrow[l - 1]: l -= 1
        rr = x
        while rr + 1 < n and binrow[rr + 1]: rr += 1
        return l, rr
    h, w = occ.shape
    y0 = int(min(max(round(cy), 0), h - 1)); x0 = int(min(max(round(cx), 0), w - 1))
    r1 = run(occ[y0, :] > 0, x0); r2 = run(occ[:, x0] > 0, y0)
    if r1 is None or r2 is None: return None
    return dict(cx=cx, cy=cy, xl=r1[0], xr=r1[1], yt=r2[0], yb=r2[1],
                m1=(r1[1] - r1[0] + 1) * scale, m2=(r2[1] - r2[0] + 1) * scale)
