#!/usr/bin/env python3
"""SAM v2: adaptive plug-centred prompt + no-growth fallback."""
from __future__ import annotations
import argparse, csv, math, os, sys
from pathlib import Path

_sam_libs_env = os.environ.get("COLONY_SAM_LIBS")
if _sam_libs_env and _sam_libs_env not in sys.path:
    sys.path.insert(0, _sam_libs_env)

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

_skill_scripts = str(Path(__file__).resolve().parent)
if _skill_scripts not in sys.path:
    sys.path.insert(0, _skill_scripts)
from measure_colony_image import detect_dish, detect_plug, imread_unicode, imwrite_unicode

def read_manifest(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        r=csv.DictReader(f); return list(r), list(r.fieldnames or [])

def load_sam(libs, checkpoint):
    if libs and libs not in sys.path: sys.path.insert(0, libs)
    import torch
    from segment_anything import sam_model_registry, SamPredictor
    sam=sam_model_registry["vit_b"](checkpoint=checkpoint); sam.to("cpu"); sam.eval()
    return SamPredictor(sam)

def bright_component(gray, dish_mask, plug_center, dish_radius):
    values=gray[dish_mask>0]
    if values.size==0: return None, float("nan"), None
    bg=np.percentile(values,55); threshold=max(bg+8, np.percentile(values,70))
    bright=np.where((gray>=threshold)&(dish_mask>0),255,0).astype(np.uint8)
    bright=cv2.morphologyEx(bright,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(5,5)),iterations=1)
    n,labels,stats,cents=cv2.connectedComponentsWithStats(bright,8)
    if n<=1: return None, float(bg), None
    px,py=plug_center; best=None
    for i in range(1,n):
        area=float(stats[i,cv2.CC_STAT_AREA])
        if area< max(20.0,0.0008*math.pi*dish_radius*dish_radius): continue
        d=math.hypot(cents[i][0]-px,cents[i][1]-py)
        score=-d/ max(dish_radius,1)+min(area/(0.1*math.pi*dish_radius*dish_radius),1.0)*0.5
        if best is None or score>best[0]: best=(score,i,area,cents[i])
    if best is None: return None, float(bg), None
    _,i,area,center=best
    return np.where(labels==i,255,0).astype(np.uint8), float(bg), center

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",required=True); ap.add_argument("--output-dir",required=True)
    ap.add_argument("--sam-libs",default=os.environ.get("COLONY_SAM_LIBS"))
    ap.add_argument("--checkpoint",default=os.environ.get("COLONY_SAM_CHECKPOINT"))
    ap.add_argument("--rays",type=int,default=4)
    ap.add_argument("--refine-ckpt", default="", help="边界细化模型路径；留空则自动用 models/refine_unet.pt")
    ap.add_argument("--no-refine", action="store_true", help="不加载细化模型（仅用 SAM）")
    args=ap.parse_args()
    rows,_=read_manifest(args.manifest)
    out=Path(args.output_dir).expanduser().resolve(); qa=out/"qa"; qa.mkdir(parents=True,exist_ok=True)
    predictor=load_sam(args.sam_libs,args.checkpoint)
    # ---- 可选：边界细化模型（与封装版一致）----
    refine_net=None
    if not args.no_refine:
        rc = args.refine_ckpt or str(Path(__file__).resolve().parent.parent / "models" / "refine_unet.pt")
        if os.path.exists(rc):
            try:
                import torch
                sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
                import engine_sam as _es
                refine_net=_es.UNet()
                refine_net.load_state_dict(torch.load(rc, map_location="cpu"))
                refine_net.eval()
                print("细化模型已加载:", rc)
            except Exception as e:
                print("细化模型加载失败，继续用纯 SAM:", e)
                refine_net=None
        else:
            print("未找到细化模型，使用纯 SAM:", rc)
    out_rows=[]
    for idx,row in enumerate(rows,1):
        path=Path(row["image_path"]).expanduser().resolve()
        if not path.exists(): continue
        image=imread_unicode(path); h,w=image.shape[:2]
        cx,cy,r,_,_,_=detect_dish(image)
        px,py,plug_r,_,plug_method=detect_plug(image,(cx,cy),r,None)
        # plug fallback: use dish centre when detection failed or radius is implausible
        if plug_method=="dish_center_fallback" or plug_r < 0.015*r or plug_r > 0.12*r:
            px,py=float(cx),float(cy); plug_method="dish_center_used"
        dish=np.zeros((h,w),np.uint8); cv2.circle(dish,(int(round(cx)),int(round(cy))),int(round(r*0.96)),255,-1)
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY).astype(np.float32)
        bright_mask,bg_mean,coarse_center=bright_component(gray,dish,(px,py),r)
        coarse_radius=math.sqrt(max(float(np.count_nonzero(bright_mask)),1.0)/math.pi) if bright_mask is not None else float(plug_r)
        suppressed = bright_mask is None or coarse_radius < max(0.08*r, 0.6*plug_r)
        if suppressed:
            # no-growth plate: use the plug itself as the colony
            radius_px=max(plug_r, 4.0/ (86.0/(2*r)) if r>0 else plug_r)
            mask=np.zeros((h,w),np.uint8); cv2.circle(mask,(int(round(px)),int(round(py))),int(round(radius_px)),255,-1)
            method="suppressed_plug_circle"; score=1.0
        else:
            rgb=cv2.cvtColor(image,cv2.COLOR_BGR2RGB); predictor.set_image(rgb)
            box_radius=float(np.clip(1.4*coarse_radius,0.20*r,0.70*r))
            box=np.array([max(0,px-box_radius),max(0,py-box_radius),min(w-1,px+box_radius),min(h-1,py+box_radius)],np.float32)
            # positive point at plug and at the brightest pixel near the plug
            pts=[[px,py]]; labs=[1]
            y0,y1=max(0,int(py-0.25*r)),min(h,int(py+0.25*r)); x0,x1=max(0,int(px-0.25*r)),min(w,int(px+0.25*r))
            if y1>y0 and x1>x0:
                local=gray[y0:y1,x0:x1]; iy,ix=np.unravel_index(int(np.argmax(local)),local.shape)
                qx,qy=x0+ix,y0+iy
                if gray[qy,qx] > bg_mean+10: pts.append([qx,qy]); labs.append(1)
            # negative points near the dish edge
            for ang in (45,135,225,315):
                bx=int(round(cx+0.85*r*math.cos(math.radians(ang)))); by=int(round(cy+0.85*r*math.sin(math.radians(ang))))
                if 0<=bx<w and 0<=by<h: pts.append([bx,by]); labs.append(0)
            masks,scores,_=predictor.predict(point_coords=np.array(pts,np.float32),point_labels=np.array(labs),box=box,multimask_output=True)
            best=None
            for i,m in enumerate(masks):
                area=float(m.sum()); frac=area/(math.pi*r*r)
                if area<=0 or frac<0.003 or frac>0.70: continue
                if not (0<=int(round(py))<h and 0<=int(round(px))<w and m[int(round(py)),int(round(px))]): continue
                mean_gray=float(gray[m].mean()); contrast=mean_gray-bg_mean
                s=float(scores[i])+0.001*contrast-0.05*abs(frac-0.20)
                if best is None or s>best[0]: best=(s,i,m,frac,mean_gray)
            if best is None:
                method="sam_failed_plug_circle"; radius_px=max(plug_r,4.0/(86.0/(2*r)) if r>0 else plug_r); mask=np.zeros((h,w),np.uint8); cv2.circle(mask,(int(round(px)),int(round(py))),int(round(radius_px)),255,-1); score=0.0
            else:
                score,_,mask,frac,mean_gray=best; method="sam_v2"
                if mean_gray < bg_mean+3:
                    # mask is mostly agar -> fall back to bright component or plug
                    if bright_mask is not None: mask=bright_mask; method="bright_component_fallback"
                    else:
                        radius_px=max(plug_r,4.0/(86.0/(2*r)) if r>0 else plug_r); mask=np.zeros((h,w),np.uint8); cv2.circle(mask,(int(round(px)),int(round(py))),int(round(radius_px)),255,-1); method="plug_circle_fallback"
        # ---- 细化 + 主连通域清理（与封装版一致）----
        if mask.dtype == bool:
            mask = mask.astype(np.uint8) * 255
        elif mask.dtype != np.uint8:
            mask = (mask > 0).astype(np.uint8) * 255
        _m8 = mask
        _mmpp = 86.0 / (2.0 * r) if r > 0 else 0.0
        if refine_net is not None and not suppressed:
            # 置信度门控：径向纹理与 SAM 一致 -> SAM 已圈对，不细化
            _skip = False
            if method == "sam_v2" and _mmpp > 0:
                try:
                    _cc0 = _es.centroid_cross(_m8, _mmpp)
                    _sam_mm = (_cc0["m1"] + _cc0["m2"]) / 2.0 if _cc0 else None
                    _rad_mm = _es._radial_mm(image, cx, cy, r, _mmpp)
                    if _sam_mm and _rad_mm and 0.80 <= (_rad_mm / max(_sam_mm, 1e-6)) <= 1.25:
                        _skip = True
                except Exception:
                    _skip = False
            if not _skip:
                try:
                    _ref, _used = _es.refine_mask(image, _m8, cx, cy, r, refine_net)
                    _a_sam = float(np.count_nonzero(_m8)); _a_ref = float(np.count_nonzero(_ref))
                    _dish_a = math.pi * r * r
                    _d_ref = 2.0 * math.sqrt(max(_a_ref, 1.0) / math.pi) * _mmpp
                    if (method == "sam_v2" and _a_sam > 0 and _dish_a > 0
                            and ((_a_ref > 0.60 * _dish_a and _a_sam < 0.25 * _dish_a)
                                 or _d_ref > 86.0 * 0.98)):
                        mask = _m8
                    else:
                        mask = _es.clean_mask(_ref, px, py)
                        method += "+refine"
                except Exception as e:
                    print("  细化失败，保留 SAM 结果:", e)
        contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        contour=max(contours,key=cv2.contourArea) if contours else None
        ecd=2*math.sqrt(float(np.count_nonzero(mask))/math.pi)*(86/(2*r)) if r>0 else 0.0
        # ray diameters from plug/dish centre
        vals=[]
        for k in range(args.rays//2):
            ang=math.pi*k/(args.rays//2); dist=[]
            for direction in (0,math.pi):
                dx,dy=math.cos(ang+direction),math.sin(ang+direction); last=0.0
                for t in range(1,int(r*0.98)):
                    x=int(round(px+dx*t)); y=int(round(py+dy*t))
                    if x<0 or y<0 or x>=w or y>=h: break
                    if mask[y,x]: last=float(t)
                dist.append(last)
            vals.append((dist[0]+dist[1])*(86/(2*r)))
        while len(vals)<6: vals.append(0.0)
        vals=vals[:6]
        vis=image.copy()
        if contour is not None: cv2.drawContours(vis,[contour],-1,(0,200,0),3)
        cv2.circle(vis,(int(round(px)),int(round(py))),max(3,int(round(plug_r))),(255,0,255),2)
        imwrite_unicode(qa/f"{path.stem}_annotated.jpg",vis); imwrite_unicode(qa/f"{path.stem}_mask.png",mask)
        row_out=dict(row); row_out.update({"unit":"mm","dish_diameter_mm":86,"scale_mm_per_px":86/(2*r) if r>0 else "","plug_method":plug_method,"boundary_engine":"sam_v2","boundary_method":method,"equivalent_diameter_mm":round(ecd,4)})
        for i,v in enumerate(vals,1): row_out[f"m{i}"]="" if v<=0 else f"{v:.4f}"
        out_rows.append(row_out)
        print(f"[{idx}/{len(rows)}] {path.name} -> {ecd:.2f} mm ({method})",flush=True)
    fields=list(out_rows[0].keys()) if out_rows else []
    with (out/"measurements_auto.csv").open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(out_rows)
    print("measurements:",out/"measurements_auto.csv")

if __name__=="__main__": main()
