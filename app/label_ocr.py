"""Offline OCR suggestions for photographed plate labels. Never confirms a label."""
from __future__ import annotations
import importlib.util
import re
import sys
import threading
import unicodedata
from pathlib import Path

_libs = Path(__file__).resolve().parent / 'ocrlibs'
if _libs.is_dir() and str(_libs) not in sys.path:
    # Keep the measurement engine's numpy/torch ahead of optional OCR packages.
    sys.path.append(str(_libs))
_ENGINE = None
_LOCK = threading.Lock()


def availability():
    found = importlib.util.find_spec('rapidocr_onnxruntime') is not None
    return found, ('离线数字识别已就绪；识别结果需核对' if found else
                   '未安装 OCR 组件，可直接手动选择编号；完整版已内置离线 OCR')


def parse_text(text):
    text = unicodedata.normalize('NFKC', str(text)).strip()
    text = re.sub(r'\s+', '', text).replace('—', '-').replace('–', '-')
    m = re.fullmatch(r'(\d+)(?:[-_/](\d+))?', text, re.ASCII)
    if not m or (m.group(2) and int(m.group(2)) < 1):
        return '', ''
    return m.group(1), str(int(m.group(2))) if m.group(2) else ''


def select_candidate(readings, codes, threshold=0.75):
    """Scores are model scores, not calibrated probabilities. Ambiguity abstains."""
    codes = set(map(str, codes))
    valid = {}
    for text, score in readings:
        code, rep = parse_text(text)
        if code in codes and float(score) >= threshold:
            valid[(code, rep)] = max(float(score), valid.get((code, rep), 0))
    raw = '；'.join(dict.fromkeys(str(t) for t, _ in readings))[:500]
    if len(valid) != 1:
        return dict(code='', replicate='', ocr_text=raw, ocr_score=None,
                    reason='检测到多个不同编号，请框选编号区域或手动选择' if valid else
                    '未读到清晰且已登记的编号，请手动选择或框选重试')
    (code, rep), score = next(iter(valid.items()))
    return dict(code=code, replicate=rep, ocr_text=raw, ocr_score=round(score, 4),
                reason='识别建议，尚未人工确认')


def recognize(path, codes, roi=None):
    import cv2
    import numpy as np
    from PIL import Image, ImageOps
    global _ENGINE
    if not codes:
        raise ValueError('请先填写并保存编号对应表')
    with Image.open(path) as src:
        img = ImageOps.exif_transpose(src).convert('RGB')
        if roi is not None:
            if len(roi) != 4:
                raise ValueError('框选区域需要 x、y、宽、高')
            x, y, w, h = map(float, roi)
            if not all(0 <= v <= 1 for v in (x, y, w, h)) or w <= 0 or h <= 0 or x+w > 1.00001 or y+h > 1.00001:
                raise ValueError('框选区域必须位于照片内')
            img = img.crop((round(x*img.width), round(y*img.height),
                            round((x+w)*img.width), round((y+h)*img.height)))
        if min(img.size) < 8:
            raise ValueError('编号区域太小，请重新框选')
        img.thumbnail((1800, 1800))
        bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    readings = []
    with _LOCK:
        if _ENGINE is None:
            from rapidocr_onnxruntime import RapidOCR
            _ENGINE = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)
        for k in range(4):
            frame = np.ascontiguousarray(np.rot90(bgr, k))
            result, _ = _ENGINE(frame, use_cls=True)
            for row in result or []:
                readings.append((row[1], float(row[2])))
            if roi is not None:
                # Tight crops with an isolated digit may not trigger the text detector.
                rec, _ = _ENGINE(frame, use_det=False, use_cls=True)
                for row in rec or []:
                    readings.append((row[0], float(row[1])))
    return select_candidate(readings, codes)
