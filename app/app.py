# -*- coding: utf-8 -*-
"""皿内菌落直径测量 v1.0  ——  本地网页界面版（不依赖 Tcl/Tk）

流程: 选择文件夹 -> 自动测量 -> 浏览器里看结果/人工修正 -> 导出 Excel
"""
from __future__ import annotations
import base64, csv, io, json, os, sys, threading, time, webbrowser, math
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
import ctypes
from ctypes import wintypes
from pathlib import Path as _P

APP_DIR = _P(__file__).resolve().parent
# 关键：先把 samlibs（内含 numpy 1.26 + torch）挂到最前，再导入 numpy / cv2，
# 否则会加载 numpy 2.x 导致 torch 不可用
_SAMLIB = APP_DIR / "samlibs"
if _SAMLIB.is_dir() and str(_SAMLIB) not in sys.path:
    sys.path.insert(0, str(_SAMLIB))

import numpy as np, cv2          # noqa: E402
from PIL import Image            # noqa: E402

sys.path.insert(0, str(APP_DIR / "lib"))
sys.path.insert(0, str(APP_DIR))
from measure_colony_image import imread_unicode, detect_dish
from radial_texture_engine import radial_boundary, centroid_cross
import excel_export
import ui_result                      # 面向最终用户的「测量结果」页面

APP_NAME = "皿内菌落直径测量 v2.0（高精度版）"
MODE = os.environ.get("COLONY_MODE", "hq")      # hq = SAM+细化 ; fast = 径向纹理
# 默认给最终用户看"测量结果"页；加 --annotate 则显示"人工标注"页（训练用）
ANNOTATE = ("--annotate" in sys.argv) or (os.environ.get("COLONY_UI") == "annotate")
try:
    import engine_sam
except Exception as _e:
    engine_sam = None
    print("高精度引擎不可用，将使用快速模式：", _e)
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
OUT_NAME = "菌落测量输出"          # 程序自己的输出目录，扫描时排除，避免把掩膜当输入图
WORK_DIM = 1400
DISH_MM = float(os.environ.get("COLONY_DISH_MM", "86"))
PLUG_MM = float(os.environ.get("COLONY_PLUG_MM", "6"))

# ------------------------------------------------------------------ 测量
class Measure:
    def __init__(self, path, dish_mm=DISH_MM, plug_mm=PLUG_MM):
        self.path = Path(path); self.dish_mm = dish_mm; self.plug_mm = plug_mm
        self.error = ""; self.note = ""; self.manual_mm = None; self.manual_center = None
        self.manual_m = None; self.manual_cross = None
        self.ok = False; self.mask = None; self.method = ""
        img = imread_unicode(self.path)
        self.orig_shape = img.shape[:2]
        h0, w0 = img.shape[:2]
        if max(h0, w0) > WORK_DIM:
            sc = WORK_DIM / max(h0, w0)
            img = cv2.resize(img, (int(round(w0*sc)), int(round(h0*sc))), interpolation=cv2.INTER_AREA)
        self.img = img; self.h, self.w = img.shape[:2]
        if MODE == "hq" and engine_sam is not None and engine_sam._ENGINE.get("sam") is not None or (MODE == "hq" and engine_sam is not None):
            try:
                r = engine_sam.measure_image(img, dish_mm, plug_mm)
                if not r.get("ok"):
                    self.error = r.get("error", "测量失败"); return
                self.img = r["img"]; self.h, self.w = self.img.shape[:2]
                self.cx, self.cy, self.r = r["cx"], r["cy"], r["r"]
                self.mmpp = r["mmpp"]; self.mask = r["mask"]
                self.m1, self.m2, self.diam = r["m1"], r["m2"], r["diam"]
                self.ccx, self.ccy = r["ccx"], r["ccy"]
                self.note = r.get("note", ""); self.method = r.get("method", "")
                self.ok = True
                return
            except Exception as e:
                self.error = f"高精度识别失败（{e}），可在界面手动输入直径"; return
        cx, cy, rad, _, _, _ = detect_dish(img)
        if rad <= 0:
            self.error = "未检测到培养皿"; self.cx = self.cy = self.r = 0; self.mmpp = dish_mm/100.0; return
        self.cx, self.cy, self.r = float(cx), float(cy), float(rad)
        self.mmpp = dish_mm/(2.0*rad)
        rb = radial_boundary(img, self.cx, self.cy, self.r)
        if rb is None:
            self.error = "边界识别失败，请人工输入直径"; return
        self.mask = rb["mask"]
        cc = centroid_cross(self.mask, self.mmpp)
        if cc is None:
            self.error = "量取失败，请人工输入直径"; return
        self.m1, self.m2 = cc["m1"], cc["m2"]
        self.ccx, self.ccy = cc["cx"], cc["cy"]
        self.diam = (self.m1 + self.m2)/2.0
        self.method = "径向纹理"
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        yy, xx = np.mgrid[0:self.h, 0:self.w]
        dd = np.sqrt((xx-self.cx)**2 + (yy-self.cy)**2)
        near = (dd <= 0.35*self.r); far = (dd >= 0.70*self.r) & (dd <= 0.88*self.r)
        contrast = abs(float(np.median(g[near])) - float(np.median(g[far]))) if near.sum() > 50 and far.sum() > 50 else 255
        frac = float(np.count_nonzero(self.mask))/(math.pi*self.r*self.r)
        notes = []
        if frac < 0.08: notes.append("生长很小/疑似无明显生长，请确认")
        if contrast < 25: notes.append("菌落与培养基对比度低")
        if self.diam > dish_mm: notes.append("直径超过皿径，请核对")
        self.note = "；".join(notes); self.ok = True

    def final_mm(self): return self.manual_mm if self.manual_mm is not None else (self.diam if self.ok else None)
    def final_m(self):
        if self.manual_m is not None: return self.manual_m
        if self.manual_mm is not None: return self.manual_mm, self.manual_mm
        return getattr(self, "m1", 0.0), getattr(self, "m2", 0.0)
    def status(self):
        if self.manual_mm is not None: return "人工修正"
        if not self.ok: return "失败"
        return "需复核" if self.note else "自动"
    def overlay(self, scale=1.0):
        """只画两种线：绿色菌落边界圈 + 黄色十字量取线。
        线宽按图幅成比例，保证在结果页缩略图上足够显眼。"""
        vis = self.img.copy()
        maxdim = max(1, int(max(self.h, self.w)))
        gw = max(4, int(round(maxdim * 0.010)))     # 绿圈线宽
        yw = max(3, int(round(maxdim * 0.008)))     # 黄十字线宽
        # ---------- 绿圈：优先画真实分割轮廓 ----------
        drew = False
        if self.ok and getattr(self, "mask", None) is not None:
            try:
                cnts = cv2.findContours(self.mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
                cnts = [c for c in cnts if cv2.contourArea(c) > 4]
                if cnts:
                    # 只画「主要菌落」这一条闭合边界：
                    # 优先取包含菌落中心的轮廓，否则取面积最大的那条
                    cx0 = getattr(self, "ccx", None)
                    cy0 = getattr(self, "ccy", None)
                    best = None
                    if cx0 is not None and cy0 is not None:
                        for c in sorted(cnts, key=cv2.contourArea, reverse=True):
                            try:
                                if cv2.pointPolygonTest(c, (float(cx0), float(cy0)), False) >= 0:
                                    best = c; break
                            except Exception:
                                pass
                    if best is None:
                        best = max(cnts, key=cv2.contourArea)
                    cv2.drawContours(vis, [best], -1, (0, 200, 0), gw)
                    drew = True
            except Exception:
                drew = False
        if not drew:
            cx = self.manual_center[0] if self.manual_center else getattr(self, "ccx", getattr(self, "cx", 0))
            cy = self.manual_center[1] if self.manual_center else getattr(self, "ccy", getattr(self, "cy", 0))
            mm = self.final_mm() or 0.0
            rr = max(2, int(round((mm / 2.0) / max(1e-9, self.mmpp))))
            cv2.circle(vis, (int(cx), int(cy)), rr, (0, 200, 0), gw)
        # ---------- 黄十字：人工测量优先 ----------
        if getattr(self, "manual_cross", None):
            (x1, y1, x2, y2), (x3, y3, x4, y4) = self.manual_cross
            cv2.line(vis, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 255), yw)
            cv2.line(vis, (int(x3), int(y3)), (int(x4), int(y4)), (0, 255, 255), yw)
        elif self.ok:
            m1, m2 = self.final_m()
            cx = getattr(self, "ccx", getattr(self, "cx", 0))
            cy = getattr(self, "ccy", getattr(self, "cy", 0))
            h1 = int(round((float(m1) / 2.0) / max(1e-9, self.mmpp)))
            h2 = int(round((float(m2) / 2.0) / max(1e-9, self.mmpp)))
            if h1 > 0:
                cv2.line(vis, (int(cx-h1), int(cy)), (int(cx+h1), int(cy)), (0, 255, 255), yw)
            if h2 > 0:
                cv2.line(vis, (int(cx), int(cy-h2)), (int(cx), int(cy+h2)), (0, 255, 255), yw)
        return cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)

# ------------------------------------------------------------------ 文件夹选择（ctypes，无需 Tk）
def _msgbox(text, title, flags):
    u = ctypes.windll.user32
    u.MessageBoxW.restype = ctypes.c_int
    u.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    return u.MessageBoxW(None, text, title, flags)


def _console_hwnd():
    """取当前控制台窗口句柄，用作文件夹弹窗的 owner。
    否则双击运行时对话框容易弹在 CMD 窗口后面，看起来像卡死。"""
    try:
        return ctypes.windll.kernel32.GetConsoleWindow() or None
    except Exception:
        return None


def pick_folder(title="请选择存放培养皿照片的文件夹"):
    """弹出 Windows 文件夹选择对话框（纯 ctypes，不依赖 Tk）。取消返回 None。"""
    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32

    class BROWSEINFO(ctypes.Structure):
        _fields_ = [("hwndOwner", wintypes.HWND),
                    ("pidlRoot", ctypes.c_void_p),
                    ("pszDisplayName", ctypes.c_void_p),   # 输出缓冲区用 void_p，不能用 c_wchar_p
                    ("lpszTitle", ctypes.c_wchar_p),
                    ("ulFlags", ctypes.c_uint),
                    ("lpfn", ctypes.c_void_p),
                    ("lParam", ctypes.c_void_p),
                    ("iImage", ctypes.c_int)]

    BIF_RETURNONLYFSDIRS, BIF_NEWDIALOGSTYLE, BIF_EDITBOX = 0x0001, 0x0040, 0x0010
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFO)]
    shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]

    buf = ctypes.create_unicode_buffer(260)
    bi = BROWSEINFO()
    owner = _console_hwnd()
    bi.hwndOwner = owner
    bi.pidlRoot = None
    bi.pszDisplayName = ctypes.cast(buf, ctypes.c_void_p)
    bi.lpszTitle = title
    bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_EDITBOX
    bi.lpfn = None
    bi.lParam = None
    bi.iImage = 0

    print("请在弹窗中选择存放培养皿照片的文件夹 …", flush=True)
    print("（若没看到弹窗，按 Alt+Tab 切换一下窗口即可）", flush=True)
    if owner:
        try:
            ctypes.windll.user32.SetForegroundWindow(owner)
        except Exception:
            pass
    pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
    if not pidl:
        return None
    path = ctypes.create_unicode_buffer(260)
    shell32.SHGetPathFromIDListW(pidl, path)
    try:
        ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
    except Exception:
        pass
    return path.value or None


# ------------------------------------------------------------------ 数据装配 / 页面
def build_cards(files, measures):
    """只保留结果页需要的字段：图片缩略图 + 直径 + 状态"""
    cards = []
    for idx, (p, m) in enumerate(zip(files, measures)):
        try:
            im = Image.fromarray(m.overlay())
        except Exception:
            try:
                im = Image.fromarray(cv2.cvtColor(m.img, cv2.COLOR_BGR2RGB))
            except Exception:
                continue
        im.thumbnail((640, 640))
        buf = io.BytesIO(); im.save(buf, format="JPEG", quality=82)
        b64 = base64.b64encode(buf.getvalue()).decode()
        mm = m.final_mm()
        m1, m2 = m.final_m()
        note = getattr(m, "note", "") or getattr(m, "error", "")
        try:
            with Image.open(p) as o:
                ow, oh = o.size
        except Exception:
            ow, oh = im.width, im.height
        sc = im.width / max(1, getattr(m, "w", im.width))
        ywt = max(2, int(round(0.008 * max(im.width, im.height))))   # 黄线在缩略图坐标下的线宽
        _mmpp = float(getattr(m, "mmpp", 0) or 0)
        mpt = round(_mmpp / sc, 6) if sc else 0     # 缩略图坐标下 1 像素 = 多少 mm
        cards.append(dict(
            # --- 结果页字段 ---
            key=f"{idx:05d}|{p.name}", stem=p.stem, name=p.name, folder=p.parent.name, b64=b64,
            mm=(None if mm is None else round(float(mm), 3)),
            m1=(None if not m1 else round(float(m1), 3)),
            m2=(None if not m2 else round(float(m2), 3)), note=note,
            # --- 标注页字段（--annotate 时用）---
            file=p.name, topic=p.parent.name, treat=p.stem,
            w=im.width, h=im.height, ow=ow, oh=oh,
            mmpp=round(getattr(m, "mmpp", 0) or 0, 6),
            mean=(None if mm is None else round(float(mm), 2)),
            dcx=round(getattr(m, "cx", 0)*sc, 1), dcy=round(getattr(m, "cy", 0)*sc, 1),
            dr=round(getattr(m, "r", 0)*sc, 1), plate_mm=DISH_MM, _sc=sc, ywt=ywt, mpt=mpt))
    return cards


def make_page(cards, folder, title="菌落直径测量结果"):
    if ANNOTATE:
        try:
            from ui_template import PAGE          # 训练用的标注页（画边界 + 导出CSV）
            data = json.dumps(cards, ensure_ascii=False).replace("</", "<\\/")
            tags = ["边界正确", "圈太小", "圈太大", "细毛满皿", "长条形", "中心偏移", "抑制型=菌饼"]
            return (PAGE.replace("__TAGS__", json.dumps(tags, ensure_ascii=False))
                        .replace("__DATA__", data)
                        .replace("__TITLEJSON__", json.dumps(title, ensure_ascii=False))
                        .replace("__TITLE__", title))
        except Exception as e:
            print("标注页不可用，改用结果页：", e)
    return ui_result.build_page(cards, folder, DISH_MM, title, plug_mm=PLUG_MM)


# ------------------------------------------------------------------ HTTP
STATE = {"page": "", "folder": "", "cards": [], "measures": [], "files": []}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            # 按最新的 STATE 重建页面：刷新浏览器不会丢掉人工测量/修正过的值
            try:
                if STATE.get("cards"):
                    STATE["page"] = make_page(STATE["cards"], STATE["folder"])
            except Exception:
                pass
            body = STATE["page"].encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        elif p == "/favicon.ico":
            self.send_response(204); self.end_headers()
        else:
            self.send_response(404); self.end_headers()
    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/pick":
            try:
                f = pick_folder()
                if not f: self._json({"ok": False, "error": ""}); return
                load_folder(f)
                self._json({"ok": True, "folder": f})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        if path == "/manual":
            n = int(self.headers.get("Content-Length", 0))
            try:
                req = json.loads(self.rfile.read(n).decode("utf-8"))
                key = req.get("key") or req.get("stem", "")
                pts = req.get("points") or []
                idx = next((i for i, c in enumerate(STATE["cards"]) if c.get("key") == key), None)
                if idx is None:
                    self._json({"ok": False, "error": "找不到该图片"}); return
                if len(pts) != 4:
                    self._json({"ok": False, "error": "需要 4 个点（横线 2 点 + 竖线 2 点）"}); return
                m = STATE["measures"][idx]
                sc = STATE["cards"][idx].get("_sc") or 1.0
                P = [(float(p[0]) / sc, float(p[1]) / sc) for p in pts]
                m1 = math.hypot(P[0][0]-P[1][0], P[0][1]-P[1][1]) * m.mmpp
                m2 = math.hypot(P[2][0]-P[3][0], P[2][1]-P[3][1]) * m.mmpp
                m.manual_m = (m1, m2)
                m.manual_mm = (m1 + m2) / 2.0
                m.manual_cross = ((P[0][0], P[0][1], P[1][0], P[1][1]),
                                  (P[2][0], P[2][1], P[3][0], P[3][1]))
                m.manual_center = (sum(p[0] for p in P) / 4.0, sum(p[1] for p in P) / 4.0)
                b64 = STATE["cards"][idx].get("b64", "")
                try:
                    im = Image.fromarray(m.overlay()); im.thumbnail((640, 640))
                    buf = io.BytesIO(); im.save(buf, format="JPEG", quality=82)
                    b64 = base64.b64encode(buf.getvalue()).decode()
                except Exception:
                    pass
                STATE["cards"][idx].update(b64=b64, mm=round(m.manual_mm, 3),
                                           m1=round(m1, 3), m2=round(m2, 3), note="")
                self._json({"ok": True, "key": key, "mm": round(m.manual_mm, 3),
                            "m1": round(m1, 3), "m2": round(m2, 3), "b64": b64})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return
        if path != "/export":
            self.send_response(404); self.end_headers(); return
        n = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(n).decode("utf-8"))
            out = Path(STATE["folder"]) / f"菌落直径测量结果_{time.strftime('%Y%m%d_%H%M')}.xlsx"
            items = []
            bykey = {c.get("key"): m for c, m in zip(STATE["cards"], STATE["measures"])}
            for row in payload:
                cls = type("It", (), {})()
                k = row.get("key") or row.get("stem")
                ref = bykey.get(k)
                cls.path = getattr(ref, "path", None) or (Path(STATE["folder"]) / str(k))
                cls.final_mm = (lambda v: (lambda: v))(row["mm"])
                cls.final_m = (lambda a, b: (lambda: (a, b)))(row.get("m1") or 0, row.get("m2") or 0)
                cls.status = (lambda s: (lambda: s))(row.get("status", ""))
                cls.note = row.get("note", ""); cls.error = ""
                items.append(cls)
            excel_export.write_xlsx(str(out), items, DISH_MM, PLUG_MM)
            self._json({"ok": True, "path": str(out)})
        except Exception as e:
            self._json({"ok": False, "error": str(e)})
    def _json(self, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

def free_port(start=8765):
    import socket
    for p in range(start, start+50):
        with socket.socket() as s:
            try: s.bind(("127.0.0.1", p)); return p
            except OSError: continue
    return start

def load_folder(folder):
    """测量整个文件夹并刷新页面状态"""
    folder = str(Path(folder).resolve())
    files = sorted([p for p in Path(folder).rglob("*")
                    if p.is_file() and p.suffix.lower() in IMG_EXT
                    and OUT_NAME not in p.parts and "auto_masks" not in p.parts],
                   key=lambda p: (str(p.parent), p.name))
    if not files:
        raise RuntimeError("该文件夹（含子文件夹）中没有找到图片")
    print(f"共 {len(files)} 张图片，开始测量 ...")
    measures = []
    for i, p in enumerate(files, 1):
        try:
            m = Measure(p)
        except Exception as e:
            m = Measure.__new__(Measure); m.path = p; m.ok = False; m.error = f"读取失败: {e}"
            m.manual_mm = None; m.manual_m = None; m.manual_cross = None; m.manual_center = None
            m.note = ""; m.mmpp = 1.0; m.cx = m.cy = m.r = 0
        measures.append(m)
        if i % 20 == 0 or i == len(files): print(f"  {i}/{len(files)}")
    # ---- 保存自动掩膜与汇总，供后续训练使用 ----
    try:
        od = Path(folder) / OUT_NAME
        (od / "auto_masks").mkdir(parents=True, exist_ok=True)
        with (od / "measurements.csv").open("w", newline="", encoding="utf-8-sig") as fh:
            wr = csv.writer(fh)
            wr.writerow(["原图", "直径mm", "m1", "m2", "识别方式", "状态", "提示", "自动掩膜"])
            for p_, m_ in zip(files, measures):
                mm = m_.final_mm()
                m1, m2 = m_.final_m()
                if getattr(m_, "mask", None) is not None:
                    try:
                        cv2.imencode(".png", m_.mask)[1].tofile(str(od / "auto_masks" / f"{p_.stem}.png"))
                    except Exception:
                        pass
                wr.writerow([p_.name, "" if mm is None else round(mm, 3),
                             round(m1, 3), round(m2, 3), getattr(m_, "method", ""),
                             m_.status(), getattr(m_, "note", "") or getattr(m_, "error", ""),
                             f"auto_masks/{p_.stem}.png"])
        print(f"自动掩膜与汇总已保存到：{od}")
    except Exception as e:
        print("保存掩膜失败（不影响测量）：", e)

    cards = build_cards(files, measures)
    STATE.update(folder=folder, files=files, measures=measures, cards=cards,
                 page=make_page(cards, folder))
    try: (APP_DIR / "config.json").write_text(json.dumps({"folder": folder}, ensure_ascii=False), encoding="utf-8")
    except Exception: pass
    return len(files)

def main():
    print("=" * 62, flush=True)
    print("  " + APP_NAME, flush=True)
    print("  正在启动，请稍候；运行期间请不要关闭本窗口。", flush=True)
    print("=" * 62, flush=True)
    cfg = APP_DIR / "config.json"
    last = ""
    try:
        if cfg.exists(): last = json.loads(cfg.read_text(encoding="utf-8")).get("folder", "")
    except Exception: last = ""
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_dir():
        folder = sys.argv[1]
    elif last and Path(last).is_dir():
        # 不静默加载：明确询问，避免"打开就跑"的困惑
        ans = _msgbox(f"上次使用的文件夹是：\n{last}\n\n是否继续测量这个文件夹？\n\n"
                      "【是】= 继续    【否】= 重新选择文件夹", APP_NAME, 0x04 | 0x20 | 0x100)
        folder = last if ans == 6 else pick_folder()
    else:
        folder = pick_folder()
    try: cfg.write_text(json.dumps({"folder": folder}, ensure_ascii=False), encoding="utf-8")
    except Exception: pass
    if not folder:
        print("未选择文件夹，程序已退出。", flush=True)
        return 2
    print("已选择文件夹：" + str(folder), flush=True)
    if MODE == "hq" and engine_sam is not None:
        try: engine_sam.load_models(log=print)
        except Exception as e: print("高精度模型加载失败，改用快速模式:", e)
    try: load_folder(folder)
    except Exception as e:
        _msgbox(str(e), APP_NAME, 0x40); return 1
    port = free_port()
    srv = HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print("界面地址:", url, "\n（关闭本窗口即结束程序）")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: pass

if __name__ == "__main__":
    sys.exit(main())
