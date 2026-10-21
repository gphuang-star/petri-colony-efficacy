# -*- coding: utf-8 -*-
"""高精度测量引擎：SAM (ViT-B) 出边界 -> 细化 U-Net 修正 -> 十字交叉量取

模型只在程序启动时加载一次；单张耗时约 15–40 秒（CPU）。
"""
from __future__ import annotations
import math, sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SAM_LIBS = APP_DIR / "samlibs"
# 必须先挂载 samlibs（内含 numpy 1.26），再导入 numpy/cv2/torch，
# 否则 numpy 2.x 与 torch 2.2 不兼容
if str(SAM_LIBS) not in sys.path:
    sys.path.insert(0, str(SAM_LIBS))

import numpy as np, cv2          # noqa: E402
import torch, torch.nn as nn, torch.nn.functional as F
from segment_anything import sam_model_registry, SamPredictor

WORK_DIM = 1400
R_TARGET = 225.0
CROP = 512

# ---------------- 细化网络（与训练时结构一致） ----------------
class UNet(nn.Module):
    def __init__(s, ci=4, base=16):
        super().__init__()
        def blk(i, o): return nn.Sequential(nn.Conv2d(i,o,3,padding=1), nn.BatchNorm2d(o), nn.ReLU(),
                                            nn.Conv2d(o,o,3,padding=1), nn.BatchNorm2d(o), nn.ReLU())
        s.e1=blk(ci,base); s.e2=blk(base,base*2); s.e3=blk(base*2,base*4); s.b=blk(base*4,base*8)
        s.u3=nn.ConvTranspose2d(base*8,base*4,2,2); s.d3=blk(base*8,base*4)
        s.u2=nn.ConvTranspose2d(base*4,base*2,2,2); s.d2=blk(base*4,base*2)
        s.u1=nn.ConvTranspose2d(base*2,base,2,2);   s.d1=blk(base*2,base)
        s.out=nn.Conv2d(base,1,1)
    def forward(s, x):
        e1=s.e1(x); e2=s.e2(F.max_pool2d(e1,2)); e3=s.e3(F.max_pool2d(e2,2)); b=s.b(F.max_pool2d(e3,2))
        return s.out(s.d1(torch.cat([s.u1(s.d2(torch.cat([s.u2(s.d3(torch.cat([s.u3(b),e3],1))),e2],1))),e1],1)))

_ENGINE = {}

def _models_dir():
    """兼容两种布局：
       · 发布包：app.py / engine_sam.py 与 models/ 同级
       · 仓库：  app/engine_sam.py，models/ 在仓库根（上一级）
    """
    for cand in (APP_DIR / "models", APP_DIR.parent / "models"):
        if cand.is_dir():
            return cand
    return APP_DIR / "models"


def load_models(sam_ckpt=None, refine_ckpt=None, log=print):
    if _ENGINE.get("sam") is not None: return _ENGINE
    torch.set_num_threads(max(2, (torch.get_num_threads() or 4)))
    _md = _models_dir()
    sam_ckpt = Path(sam_ckpt or (_md/"sam_vit_b_01ec64.pth"))
    ref_ckpt = Path(refine_ckpt or (_md/"refine_unet.pt"))
    log("正在加载高精度模型（首次约 10 秒）…")
    sam = sam_model_registry["vit_b"](checkpoint=str(sam_ckpt)); sam.to("cpu"); sam.eval()
    _ENGINE["sam"] = SamPredictor(sam)
    net = UNet()
    if ref_ckpt.exists():
        net.load_state_dict(torch.load(str(ref_ckpt), map_location="cpu")); net.eval(); _ENGINE["refiner"] = net
    else:
        _ENGINE["refiner"] = None
    log("模型加载完成")
    return _ENGINE

# ---------------- 工具 ----------------
def bright_component(gray, dish_mask, plug_center, dish_radius):
    values = gray[dish_mask > 0]
    if values.size == 0: return None, float("nan"), None
    bg = np.percentile(values, 55); thr = max(bg + 8, np.percentile(values, 70))
    bright = np.where((gray >= thr) & (dish_mask > 0), 255, 0).astype(np.uint8)
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5)), 1)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(bright, 8)
    if n <= 1: return None, float(bg), None
    px, py = plug_center; best = None
    for i in range(1, n):
        area = float(stats[i, cv2.CC_STAT_AREA])
        if area < max(20.0, 0.0008*math.pi*dish_radius*dish_radius): continue
        d = math.hypot(cents[i][0]-px, cents[i][1]-py)
        score = -d/max(dish_radius,1) + min(area/(0.1*math.pi*dish_radius*dish_radius),1.0)*0.5
        if best is None or score > best[0]: best = (score, i, area)
    if best is None: return None, float(bg), None
    return np.where(labels == best[1], 255, 0).astype(np.uint8), float(bg), None

def centroid_cross(mask, scale):
    occ = (mask > 0).astype(np.uint8)
    M = cv2.moments(occ, binaryImage=True)
    if M["m00"] <= 0: return None
    cx, cy = M["m10"]/M["m00"], M["m01"]/M["m00"]
    def run(row, x):
        n=len(row); x=int(min(max(x,0),n-1))
        if not row[x]:
            for d in range(1,n):
                if x-d>=0 and row[x-d]: x-=d; break
                if x+d<n and row[x+d]: x+=d; break
            else: return None
        l=x
        while l-1>=0 and row[l-1]: l-=1
        r=x
        while r+1<n and row[r+1]: r+=1
        return l,r
    h,w = occ.shape
    y0=int(min(max(round(cy),0),h-1)); x0=int(min(max(round(cx),0),w-1))
    r1=run(occ[y0,:]>0,x0); r2=run(occ[:,x0]>0,y0)
    if r1 is None or r2 is None: return None
    return dict(cx=cx, cy=cy, m1=(r1[1]-r1[0]+1)*scale, m2=(r2[1]-r2[0]+1)*scale)

def sam_mask(image, cx, cy, r, px, py, plug_r, predictor):
    """复刻已验证的 SAM v2 提示策略。

    关键：正样本点 / 候选框 / “掩膜必须包含该点” 全部以【菌饼中心 (px,py)】为准；
    (cx,cy)（皿心）只用来生成皿边负样本。菌落是从菌饼长出来的，
    用皿心当提示点会分到琼脂或皿边上去。"""
    h, w = image.shape[:2]
    dish = np.zeros((h,w), np.uint8); cv2.circle(dish, (int(round(cx)),int(round(cy))), int(round(r*0.96)), 255, -1)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    bright_mask, bg_mean, _ = bright_component(gray, dish, (px,py), r)
    coarse_r = math.sqrt(max(float(np.count_nonzero(bright_mask)),1.0)/math.pi) if bright_mask is not None else float(plug_r)
    if bright_mask is None or coarse_r < max(0.08*r, 0.6*plug_r):
        rad = max(plug_r, 4.0/(86.0/(2*r)) if r>0 else plug_r)
        m = np.zeros((h,w), np.uint8); cv2.circle(m, (int(round(px)),int(round(py))), int(round(rad)), 255, -1)
        return m, "suppressed_plug"
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB); predictor.set_image(rgb)
    box_r = float(np.clip(1.4*coarse_r, 0.20*r, 0.70*r))
    box = np.array([max(0,px-box_r), max(0,py-box_r), min(w-1,px+box_r), min(h-1,py+box_r)], np.float32)
    pts=[[px,py]]; labs=[1]
    y0,y1=max(0,int(py-0.25*r)),min(h,int(py+0.25*r)); x0,x1=max(0,int(px-0.25*r)),min(w,int(px+0.25*r))
    if y1>y0 and x1>x0:
        loc=gray[y0:y1,x0:x1]; iy,ix=np.unravel_index(int(np.argmax(loc)),loc.shape); qx,qy=x0+ix,y0+iy
        if gray[qy,qx] > bg_mean+10: pts.append([qx,qy]); labs.append(1)
    for ang in (45,135,225,315):
        bx=int(round(cx+0.85*r*math.cos(math.radians(ang)))); by=int(round(cy+0.85*r*math.sin(math.radians(ang))))
        if 0<=bx<w and 0<=by<h: pts.append([bx,by]); labs.append(0)
    masks, scores, _ = predictor.predict(point_coords=np.array(pts,np.float32),
                                        point_labels=np.array(labs), box=box, multimask_output=True)
    best=None
    for i,m in enumerate(masks):
        area=float(m.sum()); frac=area/(math.pi*r*r)
        if area<=0 or frac<0.003 or frac>0.70: continue
        if not m[int(round(py)), int(round(px))]: continue          # 必须包含菌饼中心
        mean_gray=float(gray[m].mean()); s=float(scores[i])+0.001*(mean_gray-bg_mean)-0.05*abs(frac-0.20)
        if best is None or s>best[0]: best=(s,m,mean_gray)
    if best is None:
        m=np.zeros((h,w),np.uint8); cv2.circle(m,(int(round(px)),int(round(py))),int(round(max(plug_r,4.0/(86.0/(2*r))))),255,-1)
        return m, "sam_failed_plug"
    # 掩膜整体灰度接近背景（其实圈到琼脂）-> 退回亮区域或菌饼
    if best[2] < bg_mean + 3:
        if bright_mask is not None:
            return bright_mask, "bright_component_fallback"
        m=np.zeros((h,w),np.uint8); cv2.circle(m,(int(round(px)),int(round(py))),int(round(max(plug_r,4.0/(86.0/(2*r))))),255,-1)
        return m, "plug_circle_fallback"
    return (best[1].astype(np.uint8)*255), "sam"

def refine_mask(image, sam_m, cx, cy, r, net):
    """把 SAM 掩膜送进细化网络，返回修正后的掩膜（原图坐标系）"""
    if net is None: return sam_m, False
    h, w = image.shape[:2]
    k = R_TARGET/r; cs = int(round(CROP/k))
    x0, y0 = int(round(cx-cs/2)), int(round(cy-cs/2))
    def crop(im, interp):
        shp = (cs,cs) if im.ndim == 2 else (cs,cs,im.shape[2])
        c = np.zeros(shp, im.dtype)
        sx0,sy0=max(0,x0),max(0,y0); sx1,sy1=min(im.shape[1],x0+cs),min(im.shape[0],y0+cs)
        c[sy0-y0:sy1-y0, sx0-x0:sx1-x0] = im[sy0:sy1, sx0:sx1]
        return cv2.resize(c, (CROP,CROP), interpolation=interp)
    cimg = crop(image, cv2.INTER_AREA); csam = crop(sam_m, cv2.INTER_NEAREST)
    xin = np.ascontiguousarray(np.dstack([cimg.astype(np.float32)/255.0, (csam>0).astype(np.float32)]).transpose(2,0,1)[None], dtype=np.float32)
    with torch.no_grad():
        pr = torch.sigmoid(net(torch.from_numpy(xin))).numpy()[0,0]
    cpred = (cv2.resize(pr,(CROP,CROP),interpolation=cv2.INTER_LINEAR) > 0.5).astype(np.uint8)*255
    back = cv2.resize(cpred, (cs,cs), interpolation=cv2.INTER_NEAREST)
    full = np.zeros((h,w), np.uint8)
    sx0,sy0=max(0,x0),max(0,y0); sx1,sy1=min(w,x0+cs),min(h,y0+cs)
    full[sy0:sy1, sx0:sx1] = back[sy0-y0:sy1-y0, sx0-x0:sx1-x0]
    return full, True

def clean_mask(mask, cx, cy):
    """只保留包含（或最靠近）菌落中心的主连通域，并填补内部空洞。
    返回 uint8 掩膜（0/255）。"""
    m = (mask > 0).astype(np.uint8)
    if int(m.sum()) == 0:
        return mask
    n, labels, stats, cents = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return mask
    best = None
    h, w = m.shape
    xi, yi = int(round(cx)), int(round(cy))
    if 0 <= yi < h and 0 <= xi < w and labels[yi, xi] > 0:
        best = int(labels[yi, xi])                    # 含菌落中心的连通域优先
    if best is None:
        areas = stats[1:, cv2.CC_STAT_AREA]
        best = 1 + int(np.argmax(areas))              # 其次取面积最大
    out = np.where(labels == best, 255, 0).astype(np.uint8)
    # 补洞：仅当补洞后面积膨胀不夸张时才补（避免把环状误判成实心）
    try:
        filled = out.copy()
        cnts, _ = cv2.findContours(out, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if cnts:
            cv2.drawContours(filled, cnts, -1, 255, -1)
        a0 = float(np.count_nonzero(out)); a1 = float(np.count_nonzero(filled))
        if a0 > 0 and a1 / a0 <= 2.5:
            out = filled
    except Exception:
        pass
    return out


def _radial_mm(image, cx, cy, r, mmpp):
    """用径向纹理法快速估一个直径，仅用于判断 SAM 是否已经圈对。"""
    try:
        from radial_texture_engine import radial_boundary, centroid_cross
        rb = radial_boundary(image, cx, cy, r)
        if rb is None:
            return None
        cc = centroid_cross(rb["mask"], mmpp)
        return (cc["m1"] + cc["m2"]) / 2.0 if cc else None
    except Exception:
        return None


def measure_image(image, dish_mm=86.0, plug_mm=6.0, use_refiner=True, log=print):
    """返回 dict(ok, m1, m2, diam, mask, cx, cy, r, mmpp, note, method)"""
    eng = load_models(log=log)
    img = image
    h0, w0 = img.shape[:2]
    if max(h0,w0) > WORK_DIM:
        s = WORK_DIM/max(h0,w0)
        img = cv2.resize(img, (int(round(w0*s)), int(round(h0*s))), interpolation=cv2.INTER_AREA)
    from measure_colony_image import detect_dish, detect_plug
    cx, cy, r, _, _, _ = detect_dish(img)
    if r <= 0: return dict(ok=False, error="未检测到培养皿")
    mmpp = dish_mm/(2.0*r)
    px, py, pr, _, pm = detect_plug(img, (cx,cy), r, None)
    if pm == "dish_center_fallback" or pr < 0.015*r or pr > 0.12*r:
        px, py, pr = float(cx), float(cy), (plug_mm/2.0)/mmpp
    sam_m, meth = sam_mask(img, cx, cy, r, px, py, pr, eng["sam"])
    mask, used = (sam_m, False)
    if use_refiner and eng.get("refiner") is not None:
        # ---- 置信度门控 ----
        # 径向纹理估计与 SAM 面积一致 -> SAM 已经圈对，不细化（避免把对的改差）；
        # 明显大于 SAM -> 说明少圈了，才交给细化模型。
        _skip_refine = False
        if meth == "sam":
            _sam_cc = centroid_cross(sam_m, mmpp)
            _sam_mm = (_sam_cc["m1"] + _sam_cc["m2"]) / 2.0 if _sam_cc else None
            _rad_mm = _radial_mm(img, cx, cy, r, mmpp)
            if _sam_mm and _rad_mm and 0.80 <= (_rad_mm / max(_sam_mm, 1e-6)) <= 1.25:
                _skip_refine = True
        if _skip_refine:
            mask, used = sam_m, False
        else:
            try:
                m_ref, _ok = refine_mask(img, sam_m, cx, cy, r, eng["refiner"])
                a_sam = float(np.count_nonzero(sam_m)); a_ref = float(np.count_nonzero(m_ref))
                dish_a = math.pi * r * r
                # 安全阀：SAM 可信、细化却把面积吹到整个皿 -> 保留 SAM
                _d_ref = 2.0 * math.sqrt(max(a_ref, 1.0) / math.pi) * mmpp
                if (meth == "sam" and a_sam > 0 and dish_a > 0
                        and ((a_ref > 0.60 * dish_a and a_sam < 0.25 * dish_a)
                             or _d_ref > dish_mm * 0.98)):
                    mask, used = sam_m, False
                else:
                    mask, used = m_ref, True
            except Exception:
                mask, used = sam_m, False
    # 关键后处理：只保留「主菌落」连通域 + 补内部空洞。
    # 否则皿边反光/杂点为被算进来，质心被拉偏 -> 十字交叉量到错误的长度。
    mask = clean_mask(mask, px, py)          # 以菌饼中心选主连通域
    cc = centroid_cross(mask, mmpp)
    if cc is None: return dict(ok=False, error="量取失败，请人工输入直径")
    diam = (cc["m1"]+cc["m2"])/2.0
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    dd = np.sqrt((xx-cx)**2 + (yy-cy)**2)
    near = dd <= 0.35*r; far = (dd >= 0.70*r) & (dd <= 0.88*r)
    contrast = abs(float(np.median(g[near]))-float(np.median(g[far]))) if near.sum()>50 and far.sum()>50 else 255
    notes=[]
    if diam > dish_mm: notes.append("直径超过皿径，请核对")
    if contrast < 25 and diam < 0.25*dish_mm: notes.append("生长很小/对比度低，请确认")
    return dict(ok=True, m1=cc["m1"], m2=cc["m2"], diam=diam, mask=mask, cx=cx, cy=cy, r=r,
                mmpp=mmpp, img=img, note="；".join(notes),
                method=("SAM+细化" if used else meth), ccx=cc["cx"], ccy=cc["cy"])
