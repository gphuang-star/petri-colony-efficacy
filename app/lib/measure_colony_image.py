#!/usr/bin/env python3
"""Measure colony diameters from Petri-dish images.

The script detects the 86 mm dish as a scale reference, segments the colony
inside the dish, casts radial rays from the colony centre, and converts
opposite-ray distances into up to six diameter measurements.

It is an experimental measurement aid.  Always inspect the annotated QA image
and prefer manual CSV values whenever the dish or colony edge is ambiguous.

Single image:
    python measure_colony_image.py --image CK-1.jpg --output-dir out

Batch:
    python measure_colony_image.py --manifest plate_manifest.csv --output-dir out
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

ALIASES = {
    "experiment": ["experiment_id", "experiment", "exp_id", "exp", "试验编号", "实验编号"],
    "sample": ["sample_id", "colony_id", "sample", "菌落编号", "样本编号"],
    "treatment": ["treatment", "treatment_name", "treat", "处理", "处理名称"],
    "replicate": ["replicate", "rep", "replicate_id", "重复", "重复编号"],
    "image": ["image_path", "image", "photo", "photo_path", "图片", "图片路径"],
    "dish_mm": ["dish_diameter_mm", "plate_diameter_mm", "petri_diameter_mm", "培养皿直径"],
    "reference_mm": ["reference_diameter_mm", "calibration_diameter_mm", "参考直径", "标定直径"],
    "measurement_plane": ["measurement_plane", "measure_plane", "测量平面"],
    "camera_angle": ["camera_angle", "拍摄角度", "tilt_angle"],
    "scale_axis": ["scale_axis", "标定轴"],
    "dish_px": ["dish_diameter_px", "plate_diameter_px", "petri_diameter_px", "培养皿像素直径"],
    "scale": ["scale_mm_per_px", "mm_per_px", "pixel_size_mm", "标定"],
    "plug": ["plug_diameter_mm", "plug_diameter", "plug_mm", "菌饼直径"],
    "tip": ["tip_color", "tip", "枪头颜色", "枪头"],
    "center": ["center", "center_xy", "菌落中心", "中心点"],
    "roi": ["colony_roi", "roi", "菌落roi", "裁剪框"],
    "rays": ["rays", "ray_count", "射线数"],
    "method": ["method", "seg_method", "分割方法"],
    "threshold": ["threshold", "seg_threshold", "分割阈值"],
    "min_area": ["min_colony_area_px", "min_area", "最小菌落面积"],
    "main_colony_mode": ["main_colony_mode", "主菌落选择", "主菌落模式"],
    "anchor_mode": ["anchor_mode", "锚点模式", "主要菌落锚点"],
    "mask": ["mask_path", "mask", "掩膜", "掩膜路径"],
    "contamination_mode": ["contamination_mode", "污染模式", "杂菌模式"],
    "halo_mode": ["halo_mode", "外沿模式", "菌丝外沿模式"],
    "edge": ["edge_type", "colony_shape", "边缘", "边缘类型"],
    "notes": ["notes", "note", "remark", "备注"],
}


def norm_header(value: str) -> str:
    value = str(value or "").strip().lower().replace("\ufeff", "")
    value = re.sub(r"[\s\-/（）()]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_")


def find_column(headers, aliases):
    normalized = {norm_header(h): h for h in headers}
    for alias in aliases:
        key = norm_header(alias)
        if key in normalized:
            return normalized[key]
    return None


def value_of(row, column):
    if not column:
        return ""
    return str(row.get(column, "") or "").strip()


def parse_float(value, default=None):
    if value is None:
        return default
    text = str(value).strip()
    if text == "" or text.lower() in {"na", "n/a", "nan", "none", "null", "-"}:
        return default
    text = text.replace("，", ".")
    text = re.sub(r"[^\d.eE+\-]", "", text)
    try:
        result = float(text)
    except ValueError:
        return default
    return result if math.isfinite(result) else default


def parse_pair(value):
    if value is None:
        return None
    parts = re.split(r"[,;，；\s]+", str(value).strip())
    if len(parts) != 2:
        return None
    a, b = parse_float(parts[0]), parse_float(parts[1])
    if a is None or b is None:
        return None
    return float(a), float(b)


def parse_roi(value):
    if value is None:
        return None
    parts = re.split(r"[,;，；\s]+", str(value).strip())
    if len(parts) != 4:
        return None
    values = [parse_float(part) for part in parts]
    if any(v is None for v in values):
        return None
    x, y, w, h = [int(round(v)) for v in values]
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def imread_unicode(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        raise ValueError(f"图片为空或无法读取: {path}")
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"OpenCV 无法解码图片: {path}")
    return image


def imwrite_unicode(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix if path.suffix else ".png"
    ok, buffer = cv2.imencode(suffix, image)
    if not ok:
        raise ValueError(f"无法编码图片: {path}")
    buffer.tofile(str(path))


def read_manifest(path: Path):
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames:
                    raise ValueError(f"清单没有表头: {path}")
                headers = [str(h or "").strip() for h in reader.fieldnames]
                rows = []
                for raw in reader:
                    row = OrderedDict((str(k or "").strip(), "" if v is None else str(v).strip()) for k, v in raw.items())
                    if any(row.values()):
                        rows.append(row)
                return rows, headers
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法解码清单: {path}")


def border_background(image: np.ndarray) -> np.ndarray:
    border = np.concatenate([image[0], image[-1], image[:, 0], image[:, -1]], axis=0)
    return np.median(border.astype(np.float32), axis=0)


def fit_dish_ellipse(image: np.ndarray, cx: float, cy: float, radius: float):
    """Fit an ellipse to edge points near a detected dish circle."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (9, 9), 2)
    edge = cv2.Canny(blurred, 40, 120)
    ys, xs = np.where(edge > 0)
    if len(xs) < 20:
        return None
    distances = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    band = (distances >= radius * 0.88) & (distances <= radius * 1.12)
    points = np.column_stack([xs[band], ys[band]]).astype(np.float32)
    if len(points) < 20:
        return None
    (ex, ey), (axis1, axis2), angle = cv2.fitEllipse(points)
    major = float(max(axis1, axis2))
    minor = float(min(axis1, axis2))
    if major <= 0 or minor <= 0:
        return None
    return {
        "center_x": float(ex),
        "center_y": float(ey),
        "major_axis_px": major,
        "minor_axis_px": minor,
        "angle_deg": float(angle),
        "perspective_ratio": minor / major,
        "n_edge_points": int(len(points)),
    }


def detect_dish(image: np.ndarray, forced_px=None, forced_center=None):
    """Return (cx, cy, radius_px, confidence, method, ellipse_meta).

    The returned radius is half of the major axis when an ellipse is
    available.  The ellipse metadata records major/minor axes so callers can
    calibrate with the major axis instead of a perspective-biased circle.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    min_dim = min(height, width)

    if forced_px and forced_px > 0:
        center = forced_center or (width / 2.0, height / 2.0)
        meta = {
            "center_x": float(center[0]), "center_y": float(center[1]),
            "major_axis_px": float(forced_px), "minor_axis_px": float(forced_px),
            "angle_deg": 0.0, "perspective_ratio": 1.0, "n_edge_points": 0,
            "source": "forced_px",
        }
        return float(center[0]), float(center[1]), float(forced_px) / 2.0, 1.0, "forced_px", meta

    blur = cv2.GaussianBlur(gray, (9, 9), 2)
    circles = cv2.HoughCircles(
        blur,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=min_dim * 0.5,
        param1=120,
        param2=45,
        minRadius=int(min_dim * 0.33),
        maxRadius=int(min_dim * 0.56),
    )
    if circles is not None:
        candidates = np.round(circles[0]).astype(int)
        cx, cy, radius = max(candidates, key=lambda c: c[2])
        edge = cv2.Canny(blur, 40, 120)
        hits = 0
        samples = 72
        for index in range(samples):
            angle = 2 * math.pi * index / samples
            x = int(round(cx + radius * math.cos(angle)))
            y = int(round(cy + radius * math.sin(angle)))
            if 0 <= x < width and 0 <= y < height:
                x0, x1 = max(0, x - 3), min(width, x + 4)
                y0, y1 = max(0, y - 3), min(height, y + 4)
                if edge[y0:y1, x0:x1].any():
                    hits += 1
        confidence = hits / samples
        if confidence >= 0.35:
            ellipse = fit_dish_ellipse(image, float(cx), float(cy), float(radius))
            if ellipse:
                center_ok = abs(ellipse["center_x"] - cx) <= radius * 0.20 and abs(ellipse["center_y"] - cy) <= radius * 0.20
                size_ok = 0.70 <= (ellipse["major_axis_px"] / 2.0) / max(radius, 1) <= 1.40
                if center_ok and size_ok:
                    ellipse["source"] = "hough_ellipse"
                    return (
                        ellipse["center_x"], ellipse["center_y"], ellipse["major_axis_px"] / 2.0,
                        float(confidence), "hough_ellipse", ellipse,
                    )
            meta = {
                "center_x": float(cx), "center_y": float(cy),
                "major_axis_px": float(radius * 2.0), "minor_axis_px": float(radius * 2.0),
                "angle_deg": 0.0, "perspective_ratio": 1.0, "n_edge_points": 0,
                "source": "hough_circle",
            }
            return float(cx), float(cy), float(radius), float(confidence), "hough", meta

    background = border_background(image)
    diff = np.linalg.norm(image.astype(np.float32) - background, axis=2)
    diff_u8 = cv2.normalize(diff, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, threshold = cv2.threshold(diff_u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    threshold = cv2.morphologyEx(threshold, cv2.MORPH_CLOSE, kernel, iterations=2)
    threshold = cv2.morphologyEx(threshold, cv2.MORPH_OPEN, kernel, iterations=1)
    contours, _ = cv2.findContours(threshold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise RuntimeError("无法找到培养皿；请提供 --dish-diameter-px 和 --dish-center")
    contour = max(contours, key=cv2.contourArea)
    (ex, ey), (axis1, axis2), angle = cv2.fitEllipse(contour)
    major = float(max(axis1, axis2))
    minor = float(min(axis1, axis2))
    area = cv2.contourArea(contour)
    circle_area = math.pi * (major / 2.0) ** 2
    confidence = max(0.0, min(1.0, area / circle_area if circle_area > 0 else 0.0))
    meta = {
        "center_x": float(ex), "center_y": float(ey),
        "major_axis_px": major, "minor_axis_px": minor,
        "angle_deg": float(angle), "perspective_ratio": minor / major if major > 0 else 0.0,
        "n_edge_points": int(len(contour)), "source": "border_ellipse",
    }
    return float(ex), float(ey), major / 2.0, float(confidence), "border_fallback", meta


def detect_plug(image: np.ndarray, dish_center, dish_radius, args):
    """Detect the raised central agar plug; fall back to the dish centre."""
    height, width = image.shape[:2]
    cx, cy = float(dish_center[0]), float(dish_center[1])
    search = max(10, int(dish_radius * 0.35))
    x0, y0 = max(0, int(cx - search)), max(0, int(cy - search))
    x1, y1 = min(width, int(cx + search)), min(height, int(cy + search))
    if x1 <= x0 or y1 <= y0:
        return cx, cy, max(3.0, dish_radius * 0.04), 0.0, "dish_center_fallback"
    roi = image[y0:y1, x0:x1]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 5)
    min_r = max(3, int(dish_radius * 0.015))
    max_r = max(min_r + 1, int(dish_radius * 0.10))
    circles = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, dp=1.2,
        minDist=max(8, int(dish_radius * 0.05)),
        param1=120, param2=25, minRadius=min_r, maxRadius=max_r,
    )
    best = None
    if circles is not None:
        for x, y, radius in np.round(circles[0]).astype(int):
            gx, gy = x0 + int(x), y0 + int(y)
            distance = math.hypot(gx - cx, gy - cy) / max(search, 1)
            score = 1.0 - min(distance, 1.0)
            if best is None or score > best[0]:
                best = (score, gx, gy, int(radius))
    if best is None:
        return cx, cy, max(3.0, dish_radius * 0.04), 0.0, "dish_center_fallback"
    return float(best[1]), float(best[2]), float(best[3]), float(best[0]), "hough_plug"


def segment_colony(image, dish_center, dish_radius, args, roi_mask=None, plug_center=None):
    """Return main-colony mask plus candidate/contaminant metadata.

    Main-colony selection is explicit.  The default ``auto`` mode prefers a
    large, central, reasonably round component, which corresponds to the
    colony growing from the central inoculation plug.  Other components above
    the minimum area are reported as candidate contaminants and are excluded
    from the measured diameter.
    """
    height, width = image.shape[:2]
    cx, cy = int(round(dish_center[0])), int(round(dish_center[1]))
    if plug_center is None:
        plug_center = (float(cx), float(cy))
    inner_radius = int(round(dish_radius) * 0.94)

    dish_mask = np.zeros((height, width), np.uint8)
    cv2.circle(dish_mask, (cx, cy), inner_radius, 255, -1)

    Y, X = np.ogrid[:height, :width]
    distance = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
    ring = (distance >= inner_radius * 0.72) & (distance <= inner_radius * 0.96)
    ring_u8 = (ring.astype(np.uint8) * 255)

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    ring_pixels = lab[ring_u8 > 0]
    agar_color = np.median(ring_pixels, axis=0) if ring_pixels.size else np.median(lab.reshape(-1, 3), axis=0)

    color_distance = np.linalg.norm(lab - agar_color, axis=2)
    color_distance[dish_mask == 0] = 0
    distance_u8 = cv2.normalize(color_distance, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
    if args.method == "dark":
        score = 255 - gray
    elif args.method == "light":
        score = gray
    elif args.method == "vs":
        score = hsv[..., 2] - 1.5 * hsv[..., 1]
    elif args.method == "texture":
        local_mean = cv2.blur(gray.astype(np.float32), (15, 15))
        local_sq = cv2.blur(gray.astype(np.float32) ** 2, (15, 15))
        score = np.sqrt(np.maximum(local_sq - local_mean * local_mean, 0))
    else:
        score = distance_u8
    blur_size = 3 if args.method == "texture" else 7
    score = cv2.GaussianBlur(score, (blur_size, blur_size), 0)
    score[dish_mask == 0] = 0
    if score.dtype != np.uint8:
        score = cv2.normalize(score, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

    if args.threshold is not None:
        _, binary = cv2.threshold(score, int(args.threshold), 255, cv2.THRESH_BINARY)
    else:
        _, binary = cv2.threshold(score, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)
    if roi_mask is not None:
        binary[roi_mask == 0] = 0

    dish_area = math.pi * inner_radius * inner_radius
    min_area = args.min_colony_area_px
    if min_area is None:
        min_area = max(50.0, dish_area * 0.002)

    eroded_dish = cv2.erode(dish_mask, kernel, iterations=1)
    boundary_band = cv2.subtract(dish_mask, eroded_dish)

    def extract_candidates(binary_mask, source):
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary_mask, 8)
        extracted = []
        for label in range(1, num_labels):
            mask_area = float(stats[label, cv2.CC_STAT_AREA])
            if mask_area < min_area:
                continue
            component_mask = (labels == label).astype(np.uint8) * 255
            contours_local, _ = cv2.findContours(component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours_local:
                continue
            contour = max(contours_local, key=cv2.contourArea)
            contour_area = float(cv2.contourArea(contour))
            if contour_area < min_area:
                continue
            # mask_area counts actual component pixels; contour_area fills the
            # outer boundary.  A ring/rim therefore has a low fill ratio and is
            # rejected instead of being treated as a filled colony.
            fill_ratio = mask_area / contour_area if contour_area > 0 else 0.0
            if fill_ratio < 0.70:
                continue
            touches_boundary = bool(np.any(component_mask[boundary_band > 0]))
            area_fraction = mask_area / dish_area if dish_area > 0 else 0.0
            if touches_boundary and area_fraction < 0.5:
                continue
            px, py = centroids[label]
            perimeter = cv2.arcLength(contour, True)
            circularity = 4 * math.pi * contour_area / (perimeter * perimeter) if perimeter > 0 else 0.0
            center_offset = math.hypot(px - cx, py - cy) / max(inner_radius, 1)
            plug_offset = math.hypot(px - plug_center[0], py - plug_center[1]) / max(inner_radius, 1)
            plug_px, plug_py = int(round(plug_center[0])), int(round(plug_center[1]))
            contains_plug = bool(0 <= plug_px < width and 0 <= plug_py < height and component_mask[plug_py, plug_px] > 0)
            mean_b = float(np.mean(lab[..., 2][component_mask > 0])) if np.any(component_mask > 0) else float(agar_color[2])
            whiteness = float(agar_color[2] - mean_b)
            extracted.append({
                "contour": contour,
                "area": contour_area,
                "mask_area": mask_area,
                "fill_ratio": float(fill_ratio),
                "area_fraction": float(area_fraction),
                "center_x": float(px),
                "center_y": float(py),
                "center_offset": float(center_offset),
                "plug_offset": float(plug_offset),
                "contains_plug": contains_plug,
                "circularity": float(circularity),
                "touches_boundary": touches_boundary,
                "whiteness": whiteness,
                "source": source,
            })
        return extracted

    halo_mode = str(getattr(args, "halo_mode", "auto") or "auto").lower()
    halo_low_percentile = float(getattr(args, "halo_low_percentile", 25.0))
    halo_texture_percentile = float(getattr(args, "halo_texture_percentile", 25.0))
    halo_search_ratio = float(getattr(args, "halo_search_ratio", 0.12))
    core_mid_percentile = float(getattr(args, "core_score_percentile_mid", 50.0))
    core_high_percentile = float(getattr(args, "core_score_percentile_high", 70.0))
    texture_gate_factor = 0.5
    core_keep_factor = 0.10
    open_ratio = 0.04
    if halo_mode == "tight":
        halo_low_percentile = max(halo_low_percentile, 45.0)
        halo_texture_percentile = max(halo_texture_percentile, 55.0)
        halo_search_ratio = min(halo_search_ratio, 0.05)
        texture_gate_factor = 0.8
        core_keep_factor = 0.06
        open_ratio = 0.06
    elif halo_mode == "loose":
        halo_low_percentile = min(halo_low_percentile, 15.0)
        halo_texture_percentile = min(halo_texture_percentile, 15.0)
        halo_search_ratio = max(halo_search_ratio, 0.20)
        texture_gate_factor = 0.25
        core_keep_factor = 0.20
        open_ratio = 0.02

    score_values = score[dish_mask > 0]
    high_percentile = core_high_percentile
    mid_percentile = core_mid_percentile
    high_threshold = float(np.percentile(score_values, high_percentile)) if score_values.size else 255.0
    mid_threshold = float(np.percentile(score_values, mid_percentile)) if score_values.size else 255.0

    def thresholded_binary(threshold_value):
        result = np.where((score >= threshold_value) & (dish_mask > 0), 255, 0).astype(np.uint8)
        result = cv2.morphologyEx(result, cv2.MORPH_OPEN, kernel, iterations=1)
        result = cv2.morphologyEx(result, cv2.MORPH_CLOSE, kernel, iterations=2)
        if roi_mask is not None:
            result[roi_mask == 0] = 0
        return result

    binary_mid = thresholded_binary(mid_threshold)
    binary_high = thresholded_binary(high_threshold)
    candidates = (
        extract_candidates(binary, "low")
        + extract_candidates(binary_mid, "mid")
        + extract_candidates(binary_high, "high")
    )
    if not candidates:
        raise RuntimeError("菌落候选面积过小或不存在；请检查 --threshold / --colony-roi")

    max_area = max(candidate["area"] for candidate in candidates)
    central = [candidate for candidate in candidates if candidate["center_offset"] <= 0.55]
    mode = str(getattr(args, "main_colony_mode", "auto") or "auto").lower()

    def simple_score(candidate):
        plug_factor = 1.0 if candidate.get("contains_plug") else max(0.10, 1.0 - min(candidate.get("plug_offset", 1.0), 1.0))
        return (
            candidate["mask_area"]
            * (1.0 - min(candidate["center_offset"], 0.65))
            * (0.5 + 0.5 * min(candidate["circularity"], 1.0))
            * min(candidate.get("fill_ratio", 1.0), 1.0)
            * plug_factor
        )

    def select_best(source_name):
        pool_source = [candidate for candidate in candidates if candidate.get("source") == source_name]
        if not pool_source:
            return None
        plug_candidates = [candidate for candidate in pool_source if candidate.get("contains_plug")]
        if plug_candidates:
            pool_source = plug_candidates
        else:
            minimum_offset = min(candidate.get("plug_offset", 1.0) for candidate in pool_source)
            pool_source = [candidate for candidate in pool_source if candidate.get("plug_offset", 1.0) <= minimum_offset + 0.08]
        pool_central = [candidate for candidate in pool_source if candidate["center_offset"] <= 0.55]
        pool = pool_central or pool_source
        mode_local = str(getattr(args, "main_colony_mode", "auto") or "auto").lower()
        if mode_local == "largest":
            return max(pool, key=lambda candidate: candidate["mask_area"])
        if mode_local == "center":
            return max(
                pool,
                key=lambda candidate: candidate["mask_area"] / ((candidate["center_offset"] + 0.1) ** 2),
            )
        return max(pool, key=simple_score)

    main_low = select_best("low")
    main_mid = select_best("mid") or main_low
    main_high = select_best("high") or main_low
    if main_low is None and main_mid is None and main_high is None:
        raise RuntimeError("无法从低/中/高阈值候选中选择主菌落")

    # Adaptive threshold choice: plate-filling colonies need a low threshold to
    # capture the full mycelium; compact colonies use a high threshold so that
    # the dish rim/agar is not merged into the colony.
    if main_mid is not None:
        mid_area_fraction = main_mid["mask_area"] / dish_area if dish_area > 0 else 0.0
        mid_ecd_ratio = math.sqrt(max(main_mid["mask_area"], 1.0) / max(math.pi * inner_radius * inner_radius, 1.0))
    else:
        mid_area_fraction = 1.0
        mid_ecd_ratio = 1.0
    # Core selection stays adaptive in every mode; halo_mode only changes
    # how much the boundary is tightened or expanded around that core.
    if mid_area_fraction > 0.95 or mid_ecd_ratio > 0.70:
        main = main_low or main_mid or main_high
        reason = "large_colony_low_threshold"
    else:
        main = main_high or main_mid or main_low
        reason = "compact_colony_high_threshold"
    if main is None or main["mask_area"] < 0.02 * dish_area:
        main = main_low or main_mid or main_high
        reason += "_fallback"
    if main is None:
        raise RuntimeError("主菌落选择失败")

    core_mask = np.zeros((height, width), np.uint8)
    cv2.drawContours(core_mask, [main["contour"]], -1, 255, -1)
    core_radius = math.sqrt(max(main["area"], 1.0) / math.pi)

    # A texture map helps separate true mycelium (high local variation) from
    # blank agar, dish-bottom bumps and smooth reflections.
    local_mean = cv2.blur(gray.astype(np.float32), (15, 15))
    local_sq = cv2.blur(gray.astype(np.float32) ** 2, (15, 15))
    texture_map = np.sqrt(np.maximum(local_sq - local_mean * local_mean, 0))
    texture_values = texture_map[dish_mask > 0]
    texture_threshold = float(np.percentile(texture_values, halo_texture_percentile)) if texture_values.size else 0.0

    score_values = score[dish_mask > 0]
    if score_values.size:
        high_threshold = float(np.percentile(score_values, 55))
        low_threshold = float(np.percentile(score_values, halo_low_percentile))
    else:
        high_threshold = 0.0
        low_threshold = 0.0

    search_radius = max(5, int(round(halo_search_ratio * core_radius)))
    search_size = 2 * search_radius + 1
    search_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (search_size, search_size))
    core_dilated = cv2.dilate(core_mask, search_kernel, iterations=1)

    halo = np.zeros_like(core_mask)
    halo[(score >= low_threshold) & (texture_map >= texture_threshold) & (core_dilated > 0)] = 255
    expanded = cv2.bitwise_or(core_mask, halo)

    # Keep the dense core and a narrow core band; outside that band only
    # texture-supported mycelium survives.  This removes blank agar and smooth
    # dish-bottom protrusions that touch the colony boundary.
    core_seed = np.where((core_mask > 0) & (score >= high_threshold), 255, 0).astype(np.uint8)
    if np.count_nonzero(core_seed) < 0.10 * max(np.count_nonzero(core_mask), 1):
        erode_size = max(3, int(round(0.06 * core_radius)) * 2 + 1)
        core_seed = cv2.erode(
            core_mask,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erode_size, erode_size)),
            iterations=1,
        )
    core_distance = cv2.distanceTransform((core_seed == 0).astype(np.uint8), cv2.DIST_L2, 3)
    core_keep_distance = max(3.0, core_keep_factor * core_radius)
    texture_gate = (texture_map >= texture_threshold * texture_gate_factor) | (core_distance <= core_keep_distance)
    expanded = np.where((expanded > 0) & texture_gate, 255, 0).astype(np.uint8)

    open_size = max(5, int(round(open_ratio * core_radius)) * 2 + 1)
    close_size = max(5, int(round(0.02 * core_radius)) * 2 + 1)
    expanded = cv2.morphologyEx(
        expanded, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_size, open_size)), iterations=1,
    )
    expanded = cv2.morphologyEx(
        expanded, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_size, close_size)), iterations=1,
    )

    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(expanded, 8)
    if n_labels > 1:
        best_label = max(range(1, n_labels), key=lambda label: stats[label, cv2.CC_STAT_AREA])
        expanded = np.where(labels == best_label, 255, 0).astype(np.uint8)

    def mask_stats(mask):
        contours_inside, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours_inside:
            return None
        contour_inside = max(contours_inside, key=cv2.contourArea)
        area_inside = float(cv2.contourArea(contour_inside))
        moments_inside = cv2.moments(contour_inside)
        if moments_inside["m00"] == 0:
            return None
        center_x_inside = moments_inside["m10"] / moments_inside["m00"]
        center_y_inside = moments_inside["m01"] / moments_inside["m00"]
        perimeter_inside = cv2.arcLength(contour_inside, True)
        circularity_inside = 4 * math.pi * area_inside / (perimeter_inside * perimeter_inside) if perimeter_inside > 0 else 0.0
        return contour_inside, area_inside, center_x_inside, center_y_inside, circularity_inside

    stats_final = mask_stats(expanded)
    if stats_final is None:
        expanded = core_mask.copy()
        stats_final = (main["contour"], main["area"], main["center_x"], main["center_y"], main["circularity"])
    final_contour, final_area, final_cx, final_cy, final_circularity = stats_final

    merged_nearby = 0
    merge_distance = max(search_radius * 1.5, 0.05 * math.sqrt(max(final_area, 1.0) / math.pi))
    remaining = []
    for candidate in candidates:
        if candidate is main:
            continue
        candidate_area = candidate["area"]
        if candidate_area < max(min_area, 0.002 * dish_area):
            continue
        candidate_mask = np.zeros_like(core_mask)
        cv2.drawContours(candidate_mask, [candidate["contour"]], -1, 255, -1)
        candidate_texture = float(np.mean(texture_map[candidate_mask > 0])) if np.any(candidate_mask > 0) else 0.0
        center_distance = math.hypot(candidate["center_x"] - final_cx, candidate["center_y"] - final_cy)
        approximate_distance = max(
            0.0,
            center_distance - (math.sqrt(candidate_area / math.pi) + math.sqrt(final_area / math.pi)),
        )
        if (
            approximate_distance <= merge_distance
            and candidate_texture >= texture_threshold * 0.8
            and candidate_area <= 0.5 * final_area
        ):
            cv2.drawContours(expanded, [candidate["contour"]], -1, 255, -1)
            merged_nearby += 1
            stats_after_merge = mask_stats(expanded)
            if stats_after_merge:
                final_contour, final_area, final_cx, final_cy, final_circularity = stats_after_merge
        else:
            remaining.append((candidate, candidate_texture, approximate_distance))

    contamination_mode = str(getattr(args, "contamination_mode", "fungal") or "fungal").lower()
    min_contaminant_area = max(min_area, args.min_contaminant_area_fraction * dish_area)
    contaminants = []
    if contamination_mode != "off":
        for candidate, candidate_texture, approximate_distance in remaining:
            if candidate["area"] < min_contaminant_area:
                continue
            if candidate_texture < texture_threshold:
                continue
            if approximate_distance <= merge_distance:
                continue
            # Ignore rim slivers and small smooth round bubbles/protrusions;
            # they are agar/dish artifacts rather than biological contamination.
            if candidate.get("touches_boundary") and candidate.get("area_fraction", 0.0) < 0.02:
                continue
            if (
                candidate.get("circularity", 0.0) > 0.85
                and candidate.get("area_fraction", 0.0) < 0.02
                and candidate_texture < texture_threshold * 1.2
            ):
                continue
            if contamination_mode == "fungal":
                # Real fungal contamination should be a clearly textured,
                # non-trivial, non-perfectly-round hyphal colony.
                if candidate_texture < texture_threshold * 1.5:
                    continue
                if candidate.get("area_fraction", 0.0) < 0.01:
                    continue
                if candidate.get("circularity", 0.0) > 0.90:
                    continue
            candidate["contaminant_texture"] = candidate_texture
            contaminants.append(candidate)

    # Regularise the final contour: a regular colony should get a smooth
    # ellipse; an irregular colony keeps a mildly smoothed outline.
    boundary_method = "raw_contour"
    radial_cv = None
    if final_contour is not None and len(final_contour) >= 5:
        points = final_contour.reshape(-1, 2).astype(np.float32)
        radial = np.linalg.norm(points - np.array([plug_center[0], plug_center[1]], dtype=np.float32), axis=1)
        radial_cv = float(np.std(radial) / np.mean(radial)) if np.mean(radial) > 0 else 1.0
        if radial_cv <= float(getattr(args, "regularity_cv_threshold", 0.0)):
            ellipse = cv2.fitEllipse(points)
            final_contour = cv2.ellipse2Poly(
                (int(round(ellipse[0][0])), int(round(ellipse[0][1]))),
                (max(2, int(round(ellipse[1][0] / 2.0))), max(2, int(round(ellipse[1][1] / 2.0)))),
                int(round(ellipse[2])), 0, 360, 1,
            )
            boundary_method = "ellipse_fit"
        else:
            epsilon = max(1.0, 0.005 * cv2.arcLength(final_contour, True))
            final_contour = cv2.approxPolyDP(final_contour, epsilon, True)
            boundary_method = "smoothed_contour"
        main_mask = np.zeros((height, width), np.uint8)
        cv2.drawContours(main_mask, [final_contour], -1, 255, -1)
        stats_regularized = mask_stats(main_mask)
        if stats_regularized:
            final_contour, final_area, final_cx, final_cy, final_circularity = stats_regularized

    large_central = [candidate for candidate in candidates if candidate["area"] >= 0.25 * max(final_area, 1.0) and candidate["center_offset"] <= 0.55]
    ambiguous = len(large_central) > 1 or main["center_offset"] > 0.55
    if merged_nearby > 0 and contaminants:
        ambiguous = True

    main_mask = expanded
    confidence = float(np.clip(
        0.5 * min(final_circularity, 1.0)
        + 0.5 * min((final_area / dish_area) * 4.0, 1.0)
        - (0.2 if ambiguous else 0.0),
        0.0, 1.0,
    ))
    selection_info = {
        "mode": mode,
        "n_candidates": len(candidates),
        "main_area_fraction": final_area / dish_area if dish_area > 0 else 0.0,
        "main_center_offset": math.hypot(final_cx - cx, final_cy - cy) / max(inner_radius, 1),
        "main_circularity": final_circularity,
        "main_touches_boundary": main["touches_boundary"],
        "main_selection_reason": reason,
        "anchor_mode": str(getattr(args, "anchor_mode", "plug") or "plug"),
        "plug_center_x": float(plug_center[0]),
        "plug_center_y": float(plug_center[1]),
        "boundary_method": boundary_method,
        "radial_cv": radial_cv,
        "main_source": main.get("source", ""),
        "main_whiteness": float(main.get("whiteness", 0.0)),
        "mid_area_fraction": float(mid_area_fraction),
        "mid_ecd_ratio": float(mid_ecd_ratio),
        "halo_mode": halo_mode,
        "halo_search_radius_px": int(search_radius),
        "halo_texture_threshold": float(texture_threshold),
        "contamination_mode": contamination_mode,
        "halo_expanded": True,
        "merged_nearby_components": merged_nearby,
        "n_contaminants": len(contaminants),
        "contaminant_area_fractions": [round(candidate["area_fraction"], 6) for candidate in contaminants],
        "contaminant_centers": [[round(candidate["center_x"], 1), round(candidate["center_y"], 1)] for candidate in contaminants],
        "contaminant_contours": [candidate["contour"] for candidate in contaminants],
        "ambiguous": bool(ambiguous),
        "high_score_threshold": float(high_threshold),
        "low_score_threshold": float(low_threshold),
        "texture_threshold": float(texture_threshold),
        "search_radius_px": int(search_radius),
    }
    method = args.method if args.method != "auto" else "lab_distance_otsu"
    return main_mask, final_contour, (final_cx, final_cy), method, confidence, agar_color, selection_info


def measure_with_rays(mask, center, max_radius, args):
    """Return list of (angle_deg, radius_px, endpoint)."""
    cx, cy = center
    height, width = mask.shape
    n_rays = max(4, int(args.rays))
    if n_rays % 2 != 0:
        n_rays += 1
    values = []
    for index in range(n_rays):
        angle = (args.ray_offset_deg + index * 360.0 / n_rays) % 360.0
        theta = math.radians(angle)
        dx, dy = math.cos(theta), math.sin(theta)
        farthest = 0.0
        last_x, last_y = cx, cy
        limit = int(max(1, max_radius))
        for step in range(1, limit + 1):
            x = int(round(cx + dx * step))
            y = int(round(cy + dy * step))
            if x < 0 or y < 0 or x >= width or y >= height:
                break
            if mask[y, x] > 0:
                farthest = float(step)
                last_x, last_y = x, y
        values.append((angle, farthest, (last_x, last_y)))
    return values


def build_diameters(ray_values, max_radius_px, args):
    """Pair opposite rays into diameter measurements."""
    n = len(ray_values)
    diameters = []
    radius_values = [r for _, r, _ in ray_values]
    if args.ray_mode == "radial":
        for index, radius in enumerate(radius_values):
            diameters.append(2.0 * radius)
    else:
        for index in range(n // 2):
            opposite = (index + n // 2) % n
            total = ray_values[index][1] + ray_values[opposite][1]
            diameters.append(total)
    return diameters


def colony_likelihood_map(image, dish_mask, method="light"):
    """White-colony likelihood: brightness minus saturation plus texture."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    if method == "dark":
        gray = 255.0 - gray
    gray_norm = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX).astype(np.float32)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
    saturation = hsv[..., 1]
    sat_norm = cv2.normalize(saturation, None, 0, 255, cv2.NORM_MINMAX).astype(np.float32)
    local_mean = cv2.blur(gray, (15, 15))
    local_sq = cv2.blur(gray ** 2, (15, 15))
    texture = np.sqrt(np.maximum(local_sq - local_mean * local_mean, 0))
    texture_norm = cv2.normalize(texture, None, 0, 255, cv2.NORM_MINMAX).astype(np.float32)
    score = np.clip(0.65 * gray_norm - 0.25 * sat_norm + 0.10 * texture_norm, 0, 255).astype(np.uint8)
    score[dish_mask == 0] = 0
    return score


def refine_boundary_radial(image, plug_center, dish_center, dish_radius, args, score_map, plug_radius_px, coarse_mask=None):
    """Measure the colony edge along rays from the plug and fit/smooth it."""
    height, width = score_map.shape
    cx, cy = float(plug_center[0]), float(plug_center[1])
    max_radius = int(dish_radius * 0.96)
    n_rays = max(16, int(getattr(args, "radial_rays", 72)))
    angles = np.linspace(0, 2 * math.pi, n_rays, endpoint=False)
    radii = []
    for angle in angles:
        dx, dy = math.cos(angle), math.sin(angle)
        values = []
        for step in range(1, max_radius):
            x = int(round(cx + dx * step))
            y = int(round(cy + dy * step))
            if x < 0 or y < 0 or x >= width or y >= height:
                break
            values.append(float(score_map[y, x]))
        if len(values) < 10:
            radii.append(float("nan"))
            continue
        profile = np.asarray(values, dtype=np.float32)
        window = max(3, int(round(0.01 * max_radius)) | 1)
        kernel = np.ones(window, dtype=np.float32) / window
        smooth = np.convolve(profile, kernel, mode="same")
        start = max(0, int(plug_radius_px) + 2)
        if start >= len(smooth) - 5:
            radii.append(float("nan"))
            continue
        segment = smooth[start:]
        peak_index = int(np.argmax(segment))
        peak = float(segment[peak_index])
        edge = None
        # Primary: first sustained drop below 60% of the colony peak.  This
        # stops at the mycelium/agar boundary instead of continuing to the rim.
        drop_threshold = 0.60 * peak
        run_needed = max(2, int(0.005 * max_radius))
        run = 0
        for index in range(peak_index + 2, len(segment)):
            if segment[index] < drop_threshold:
                run += 1
                if run >= run_needed:
                    edge = index - run + 1
                    break
            else:
                run = 0
        if edge is None:
            derivative = np.diff(segment)
            search_start = peak_index + max(2, int(0.01 * max_radius))
            search_end = len(segment) - 1
            if search_end > search_start + 2:
                local_index = int(np.argmin(derivative[search_start:search_end])) + search_start
                if derivative[local_index] < -0.2:
                    edge = local_index
        if edge is None:
            threshold = max(float(np.percentile(segment, 70)), 0.7 * peak)
            above = np.where(segment >= threshold)[0]
            edge = int(above[-1]) if len(above) else None
        edge_radius = float(start + edge) if edge is not None else float("nan")
        max_ratio = float(getattr(args, "radial_max_radius_ratio", 0.85))
        if edge_radius > max_ratio * max_radius:
            edge_radius = float("nan")
        radii.append(edge_radius)
    radii = np.asarray(radii, dtype=np.float32)
    valid = np.isfinite(radii) & (radii > 0)
    if valid.sum() < max(8, n_rays // 4):
        return None
    median_radius = float(np.nanmedian(radii[valid]))
    radii[~valid] = median_radius
    window = max(3, int(round(n_rays * 0.05)) | 1)
    kernel = np.ones(window, dtype=np.float32) / window
    padded = np.r_[radii[-window // 2:], radii, radii[:window // 2]]
    smooth_radii = np.convolve(padded, kernel, mode="same")[window // 2:window // 2 + n_rays]
    radial_cv = float(np.std(smooth_radii) / np.mean(smooth_radii)) if np.mean(smooth_radii) > 0 else 1.0
    points = np.array(
        [[cx + radius * math.cos(angle), cy + radius * math.sin(angle)] for angle, radius in zip(angles, smooth_radii)],
        dtype=np.float32,
    )
    if radial_cv <= float(getattr(args, "regularity_cv_threshold", 0.0)):
        ellipse = cv2.fitEllipse(points)
        contour = cv2.ellipse2Poly(
            (int(round(ellipse[0][0])), int(round(ellipse[0][1]))),
            (max(2, int(round(ellipse[1][0] / 2.0))), max(2, int(round(ellipse[1][1] / 2.0)))),
            int(round(ellipse[2])), 0, 360, 1,
        )
        method = "radial_ellipse_fit"
    else:
        contour = points.astype(np.int32).reshape(-1, 1, 2)
        method = "radial_smoothed"
    mask = np.zeros((height, width), np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, -1)
    return mask, contour, method, radial_cv, float(np.mean(smooth_radii))


def refine_boundary_grabcut(image, plug_center, dish_center, dish_radius, args, coarse_mask=None):
    """Refine the colony boundary with GrabCut, seeded from the central plug."""
    height, width = image.shape[:2]
    max_size = int(getattr(args, "grabcut_max_size", 900))
    resize_scale = min(1.0, float(max_size) / max(height, width))
    if resize_scale < 1.0:
        small = cv2.resize(image, (int(round(width * resize_scale)), int(round(height * resize_scale))), interpolation=cv2.INTER_AREA)
    else:
        small = image.copy()
    small_h, small_w = small.shape[:2]
    cx = dish_center[0] * resize_scale
    cy = dish_center[1] * resize_scale
    radius = dish_radius * resize_scale
    px = plug_center[0] * resize_scale
    py = plug_center[1] * resize_scale
    dish_mask = np.zeros((small_h, small_w), np.uint8)
    cv2.circle(dish_mask, (int(round(cx)), int(round(cy))), int(round(radius * 0.96)), 255, -1)

    mask = np.full((small_h, small_w), cv2.GC_BGD, np.uint8)
    probable_radius = max(4, int(round(radius * float(getattr(args, "grabcut_fg_ratio", 0.40)))))
    sure_radius = max(3, int(round(radius * float(getattr(args, "grabcut_sure_fg_ratio", 0.08)))))
    probable_radius = max(4, probable_radius)
    sure_radius = max(3, min(sure_radius, probable_radius - 1))
    cv2.circle(mask, (int(round(px)), int(round(py))), probable_radius, cv2.GC_PR_FGD, -1)
    cv2.circle(mask, (int(round(px)), int(round(py))), sure_radius, cv2.GC_FGD, -1)
    mask[dish_mask == 0] = cv2.GC_BGD

    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)
    cv2.grabCut(
        small, mask, None, bgd_model, fgd_model,
        int(getattr(args, "grabcut_iterations", 5)), cv2.GC_INIT_WITH_MASK,
    )
    foreground = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    foreground = cv2.bitwise_and(foreground, dish_mask)

    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(foreground, 8)
    if num_labels <= 1:
        return None
    plug_label = labels[int(round(py)), int(round(px))] if 0 <= int(round(py)) < small_h and 0 <= int(round(px)) < small_w else 0
    if plug_label == 0:
        distances = [(math.hypot(centroids[label][0] - px, centroids[label][1] - py), label) for label in range(1, num_labels)]
        plug_label = min(distances)[1] if distances else 1
    foreground = np.where(labels == plug_label, 255, 0).astype(np.uint8)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    foreground = cv2.morphologyEx(foreground, cv2.MORPH_CLOSE, kernel, iterations=2)
    foreground = cv2.morphologyEx(foreground, cv2.MORPH_OPEN, kernel, iterations=1)

    if resize_scale < 1.0:
        foreground = cv2.resize(foreground, (width, height), interpolation=cv2.INTER_NEAREST)

    foreground_area = float(np.count_nonzero(foreground))
    probable_area = math.pi * probable_radius * probable_radius
    grabcut_seed_not_shrunk = bool(probable_area > 0 and foreground_area / probable_area > 0.95)

    contours_local, _ = cv2.findContours(foreground, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours_local:
        return None
    contour = max(contours_local, key=cv2.contourArea)
    points = contour.reshape(-1, 2).astype(np.float32)
    radial = np.linalg.norm(points - np.array([plug_center[0], plug_center[1]], dtype=np.float32), axis=1)
    radial_cv = float(np.std(radial) / np.mean(radial)) if np.mean(radial) > 0 else 1.0
    if radial_cv <= float(getattr(args, "regularity_cv_threshold", 0.0)):
        ellipse = cv2.fitEllipse(points)
        contour = cv2.ellipse2Poly(
            (int(round(ellipse[0][0])), int(round(ellipse[0][1]))),
            (max(2, int(round(ellipse[1][0] / 2.0))), max(2, int(round(ellipse[1][1] / 2.0)))),
            int(round(ellipse[2])), 0, 360, 1,
        )
        method = "grabcut_ellipse_fit"
    else:
        epsilon = max(1.0, 0.003 * cv2.arcLength(contour, True))
        contour = cv2.approxPolyDP(contour, epsilon, True)
        method = "grabcut_smoothed"
    if grabcut_seed_not_shrunk:
        method += "_seed_review"
    refined_mask = np.zeros((height, width), np.uint8)
    cv2.drawContours(refined_mask, [contour], -1, 255, -1)
    return refined_mask, contour, method, radial_cv, float(np.mean(radial))


def measure_one(image_path: Path, args, context=None):
    context = context or {}
    image = imread_unicode(image_path)
    height, width = image.shape[:2]

    forced_dish_px = parse_float(context.get("dish_diameter_px"))
    if forced_dish_px is None and args.dish_diameter_px:
        forced_dish_px = float(args.dish_diameter_px)
    forced_dish_center = parse_pair(context.get("dish_center")) or parse_pair(args.dish_center)
    cx, cy, radius_px, dish_confidence, dish_method, dish_meta = detect_dish(
        image, forced_px=forced_dish_px, forced_center=forced_dish_center
    )

    reference_mm = (
        parse_float(context.get("reference_mm"))
        or parse_float(context.get("dish_diameter_mm"))
        or args.reference_diameter_mm
        or args.dish_diameter_mm
    )
    measurement_plane = str(context.get("measurement_plane") or args.measurement_plane or "unspecified")
    camera_angle = str(context.get("camera_angle") or "")
    scale_axis = str(context.get("scale_axis") or args.scale_axis or "major").lower()
    major_axis_px = float(dish_meta.get("major_axis_px") or radius_px * 2.0)
    minor_axis_px = float(dish_meta.get("minor_axis_px") or radius_px * 2.0)
    perspective_ratio = float(dish_meta.get("perspective_ratio") or (minor_axis_px / major_axis_px if major_axis_px > 0 else 1.0))
    if scale_axis == "mean":
        reference_px = math.sqrt(max(major_axis_px * minor_axis_px, 1e-9))
    elif scale_axis == "minor":
        reference_px = minor_axis_px
    else:
        reference_px = major_axis_px
    scale = parse_float(context.get("scale_mm_per_px"))
    if scale is None:
        scale = args.scale_mm_per_px
    if scale is None:
        if reference_px <= 0:
            raise RuntimeError("培养皿像素直径无效")
        scale = reference_mm / reference_px

    plug_x, plug_y, plug_radius_px, plug_confidence, plug_method = detect_plug(
        image, (cx, cy), radius_px, args
    )

    roi = parse_roi(context.get("colony_roi")) or parse_roi(args.colony_roi)
    roi_mask = None
    if roi:
        x, y, w, h = roi
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(width, x + w), min(height, y + h)
        roi_mask = np.zeros((height, width), np.uint8)
        if x1 > x0 and y1 > y0:
            roi_mask[y0:y1, x0:x1] = 255

    manual_mask_path = context.get("mask") or getattr(args, "mask", None)
    if manual_mask_path:
        mask_source_path = Path(str(manual_mask_path)).expanduser()
        try:
            manual_mask = imread_unicode(mask_source_path)
            if manual_mask.ndim == 3:
                manual_mask = cv2.cvtColor(manual_mask, cv2.COLOR_BGR2GRAY)
        except Exception as exc:
            raise RuntimeError(f"无法读取手动掩膜 {mask_source_path}: {exc}") from exc
        if manual_mask.shape[:2] != (height, width):
            manual_mask = cv2.resize(manual_mask, (width, height), interpolation=cv2.INTER_NEAREST)
        mask = (manual_mask > 127).astype(np.uint8) * 255
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        if num_labels > 1:
            best_label = max(range(1, num_labels), key=lambda label: stats[label, cv2.CC_STAT_AREA])
            mask = np.where(labels == best_label, 255, 0).astype(np.uint8)
        contours_manual, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours_manual:
            raise RuntimeError("手动掩膜没有有效前景")
        contour = max(contours_manual, key=cv2.contourArea)
        moments_manual = cv2.moments(contour)
        if moments_manual["m00"] == 0:
            raise RuntimeError("手动掩膜质心无效")
        colony_center = (moments_manual["m10"] / moments_manual["m00"], moments_manual["m01"] / moments_manual["m00"])
        colony_method = "manual_mask"
        colony_confidence = 1.0
        agar_color = None
        perimeter_manual = cv2.arcLength(contour, True)
        area_manual = float(cv2.contourArea(contour))
        circularity_manual = 4 * math.pi * area_manual / (perimeter_manual * perimeter_manual) if perimeter_manual > 0 else 0.0
        selection_info = {
            "mode": "manual_mask",
            "n_candidates": 1,
            "main_area_fraction": float(np.count_nonzero(mask)) / max(math.pi * radius_px * radius_px, 1.0),
            "main_center_offset": math.hypot(colony_center[0] - cx, colony_center[1] - cy) / max(radius_px, 1.0),
            "main_circularity": float(circularity_manual),
            "main_selection_reason": "manual_mask",
            "main_source": "manual_mask",
            "boundary_method": "manual_mask",
            "radial_cv": None,
            "anchor_mode": str(getattr(args, "anchor_mode", "plug") or "plug"),
            "plug_center_x": float(plug_x),
            "plug_center_y": float(plug_y),
            "main_whiteness": 0.0,
            "mid_area_fraction": None,
            "mid_ecd_ratio": None,
            "halo_mode": "manual",
            "n_contaminants": 0,
            "contaminant_area_fractions": [],
            "contaminant_centers": [],
            "contaminant_contours": [],
            "ambiguous": False,
        }
    else:
        mask, contour, colony_center, colony_method, colony_confidence, agar_color, selection_info = segment_colony(
            image, (cx, cy), radius_px, args, roi_mask=roi_mask, plug_center=(plug_x, plug_y)
        )

    anchor_mode = str(getattr(args, "anchor_mode", "plug") or "plug").lower()
    boundary_engine = str(getattr(args, "boundary_engine", "grabcut") or "grabcut").lower()
    boundary_mode = str(getattr(args, "boundary_mode", "hybrid") or "hybrid").lower()
    component_mask = mask.copy()
    component_area = float(np.count_nonzero(component_mask)) if component_mask is not None else 0.0
    grabcut_seed_review = False
    if manual_mask_path is None and anchor_mode == "plug" and boundary_engine == "grabcut":
        refined = refine_boundary_grabcut(image, (plug_x, plug_y), (cx, cy), radius_px, args, coarse_mask=mask)
        if refined:
            mask, contour, refined_method, radial_cv_value, _ = refined
            grabcut_seed_review = "seed_review" in refined_method
            colony_method = refined_method
            selection_info["boundary_method"] = refined_method
            selection_info["radial_cv"] = radial_cv_value
            selection_info["boundary_decision"] = "grabcut_used"
            colony_confidence = max(float(selection_info.get("main_circularity", 0.0) or 0.0), 0.5)
        else:
            selection_info["boundary_decision"] = "grabcut_failed_component_fallback"
    elif manual_mask_path is None and boundary_mode in ("hybrid", "radial", "auto") and anchor_mode == "plug":
        dish_mask_radial = np.zeros((height, width), np.uint8)
        cv2.circle(dish_mask_radial, (int(round(cx)), int(round(cy))), int(round(radius_px * 0.96)), 255, -1)
        polarity = "dark" if getattr(args, "method", "auto") == "dark" else "light"
        likelihood = colony_likelihood_map(image, dish_mask_radial, method=polarity)
        refined = refine_boundary_radial(
            image, (plug_x, plug_y), (cx, cy), radius_px, args, likelihood, plug_radius_px, coarse_mask=mask
        )
        if refined:
            radial_mask, radial_contour, refined_method, radial_cv_value, _ = refined
            radial_area = float(np.count_nonzero(radial_mask))
            radial_ratio = radial_area / component_area if component_area > 0 else 0.0
            use_radial = boundary_mode == "radial" or (radial_cv_value <= 0.20 and 0.25 <= radial_ratio <= 0.80)
            if use_radial:
                mask, contour = radial_mask, radial_contour
                colony_method = refined_method
                selection_info["boundary_method"] = refined_method
                selection_info["radial_cv"] = radial_cv_value
                selection_info["boundary_decision"] = "radial_used"
                colony_confidence = max(float(selection_info.get("main_circularity", 0.0) or 0.0), 0.5)
            else:
                selection_info["boundary_decision"] = "component_fallback"
                selection_info["radial_cv"] = radial_cv_value
        else:
            selection_info["boundary_decision"] = "no_radial_candidate"
    else:
        selection_info["boundary_decision"] = "component_only"

    forced_center = parse_pair(context.get("center")) or parse_pair(args.center)
    anchor_mode = str(getattr(args, "anchor_mode", "plug") or "plug").lower()
    if forced_center:
        center = forced_center
    elif anchor_mode == "plug":
        center = (plug_x, plug_y)
    else:
        center = colony_center

    ray_values = measure_with_rays(mask, center, radius_px * 0.98, args)
    diameters_px = build_diameters(ray_values, radius_px, args)
    diameters_mm = [value * scale for value in diameters_px]
    while len(diameters_mm) < 6:
        diameters_mm.append(0.0)
    diameters_mm = diameters_mm[:6]

    valid = [value for value in diameters_mm if value > 0]
    mean_mm = float(np.mean(valid)) if valid else 0.0
    sd_mm = float(np.std(valid, ddof=1)) if len(valid) > 1 else 0.0
    cv = sd_mm / mean_mm if mean_mm > 0 else None

    area_px = float(np.count_nonzero(mask))
    equivalent_diameter_px = 2.0 * math.sqrt(area_px / math.pi) if area_px > 0 else 0.0
    equivalent_diameter_mm = equivalent_diameter_px * scale
    if contour is not None:
        (_, _), feret_radius_px = cv2.minEnclosingCircle(contour)
        feret_diameter_mm = feret_radius_px * 2.0 * scale
    else:
        feret_diameter_mm = 0.0
    cross_vs_ecd_deviation = (
        abs(mean_mm - equivalent_diameter_mm) / equivalent_diameter_mm
        if mean_mm > 0 and equivalent_diameter_mm > 0 else None
    )

    edge_type = str(context.get("edge_type") or context.get("edge") or args.edge_type or "regular")
    cv_threshold = args.irregular_cv_threshold if "irregular" in edge_type.lower() else args.cv_threshold
    reasons = []
    if cv is not None and cv > cv_threshold:
        reasons.append(f"直径 CV={cv:.3f} 超过阈值 {cv_threshold:.2f}")
    if dish_confidence < 0.35:
        reasons.append(f"培养皿检测置信度低（{dish_confidence:.2f}）")
    if perspective_ratio < 0.85:
        reasons.append(
            f"照片透视明显（短轴/长轴={perspective_ratio:.2f}）；皿底人工值与图像投影值不可直接等同"
        )
    if colony_confidence < 0.35:
        reasons.append(f"菌落分割置信度低（{colony_confidence:.2f}）")
    if any(value <= 0 for value in diameters_mm):
        reasons.append("存在未测到边缘的方向")
    if mean_mm <= 0:
        reasons.append("无法计算有效平均直径")
    if grabcut_seed_review:
        reasons.append("GrabCut 种子区域未明显收缩，可能是小菌落/无生长皿，已标记复核")
    if selection_info.get("n_contaminants", 0) > 0:
        reasons.append(f"检测到 {selection_info['n_contaminants']} 个疑似杂菌/非主菌落候选，已排除；请复核标注图")
    if selection_info.get("ambiguous"):
        reasons.append("主菌落选择存在歧义，可能存在多个大小相近的候选")
    if cross_vs_ecd_deviation is not None and cross_vs_ecd_deviation > args.ecd_review_threshold:
        reasons.append(
            f"十字直径与等效圆直径偏差 {cross_vs_ecd_deviation:.1%} 超过阈值 {args.ecd_review_threshold:.0%}"
        )

    annotated = image.copy()
    cv2.circle(annotated, (int(round(cx)), int(round(cy))), int(round(radius_px)), (255, 180, 0), 3)
    cv2.circle(annotated, (int(round(plug_x)), int(round(plug_y))), max(3, int(round(plug_radius_px))), (255, 0, 255), 2)
    cv2.putText(annotated, "PLUG", (int(round(plug_x)) + 6, int(round(plug_y)) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2)
    if contour is not None:
        cv2.drawContours(annotated, [contour], -1, (0, 200, 0), 2)
    for contaminant_contour in selection_info.get("contaminant_contours", []):
        cv2.drawContours(annotated, [contaminant_contour], -1, (0, 0, 255), 2)
        moment = cv2.moments(contaminant_contour)
        if moment["m00"]:
            tx = int(round(moment["m10"] / moment["m00"]))
            ty = int(round(moment["m01"] / moment["m00"]))
            cv2.putText(annotated, "CONT", (tx + 5, ty - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
    center_pt = (int(round(center[0])), int(round(center[1])))
    cv2.circle(annotated, center_pt, 4, (0, 0, 255), -1)

    n = len(ray_values)
    for index in range(n // 2):
        opposite = (index + n // 2) % n
        p1 = ray_values[index][2]
        p2 = ray_values[opposite][2]
        if ray_values[index][1] > 0 and ray_values[opposite][1] > 0:
            cv2.line(annotated, p1, p2, (0, 255, 255), 2)
            mid_x = int(round((p1[0] + p2[0]) / 2))
            mid_y = int(round((p1[1] + p2[1]) / 2))
            label = f"{diameters_mm[index]:.2f}" if index < len(diameters_mm) else ""
            if label:
                cv2.putText(annotated, label, (mid_x + 6, mid_y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

    return {
        "image_path": str(image_path),
        "image_width": width,
        "image_height": height,
        "dish_diameter_mm": reference_mm,
        "reference_diameter_mm": reference_mm,
        "dish_diameter_px": reference_px,
        "dish_center_x": cx,
        "dish_center_y": cy,
        "dish_confidence": dish_confidence,
        "dish_method": dish_method,
        "scale_mm_per_px": scale,
        "scale_axis": scale_axis,
        "major_axis_px": major_axis_px,
        "minor_axis_px": minor_axis_px,
        "perspective_ratio": perspective_ratio,
        "measurement_plane": measurement_plane,
        "camera_angle": camera_angle,
        "colony_center_x": center[0],
        "colony_center_y": center[1],
        "colony_method": colony_method,
        "colony_confidence": colony_confidence,
        "ray_values_px": [value[1] for value in ray_values],
        "diameter_px": diameters_px,
        "diameter_mm": diameters_mm,
        "mean_diameter_mm": mean_mm,
        "sd_mm": sd_mm,
        "cv": cv,
        "area_px": area_px,
        "equivalent_diameter_mm": equivalent_diameter_mm,
        "feret_diameter_mm": feret_diameter_mm,
        "cross_vs_ecd_deviation": cross_vs_ecd_deviation,
        "edge_type": edge_type,
        "main_colony_mode": selection_info.get("mode"),
        "n_colony_candidates": selection_info.get("n_candidates"),
        "main_area_fraction": selection_info.get("main_area_fraction"),
        "main_center_offset": selection_info.get("main_center_offset"),
        "main_circularity": selection_info.get("main_circularity"),
        "main_selection_reason": selection_info.get("main_selection_reason"),
        "main_source": selection_info.get("main_source"),
        "main_whiteness": selection_info.get("main_whiteness"),
        "anchor_mode": selection_info.get("anchor_mode", anchor_mode),
        "plug_center_x": float(plug_x),
        "plug_center_y": float(plug_y),
        "plug_radius_px": float(plug_radius_px),
        "plug_confidence": float(plug_confidence),
        "plug_method": plug_method,
        "boundary_engine": boundary_engine,
        "boundary_method": selection_info.get("boundary_method"),
        "boundary_decision": selection_info.get("boundary_decision"),
        "radial_cv": selection_info.get("radial_cv"),
        "mid_area_fraction": selection_info.get("mid_area_fraction"),
        "mid_ecd_ratio": selection_info.get("mid_ecd_ratio"),
        "n_contaminants": selection_info.get("n_contaminants"),
        "contaminant_area_fractions": selection_info.get("contaminant_area_fractions"),
        "ambiguous_main_colony": selection_info.get("ambiguous"),
        "needs_review": bool(reasons),
        "review_reasons": "; ".join(reasons),
        "annotated_image": annotated,
        "mask": mask,
    }


def build_parser():
    parser = argparse.ArgumentParser(description="从培养皿图片测量菌落直径（实验性，需人工复核标注图）。")
    parser.add_argument("--image", help="单张图片路径")
    parser.add_argument("--manifest", help="批量清单 CSV")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--dish-diameter-mm", type=float, default=86.0, help="培养皿实际直径，默认 86 mm；与人工测量平面一致时最可靠")
    parser.add_argument("--reference-diameter-mm", type=float, default=None, help="用于标定的参考直径；优先于 --dish-diameter-mm")
    parser.add_argument("--scale-axis", choices=["major", "mean", "minor"], default="major", help="用椭圆长轴/几何平均/短轴标定，默认 major")
    parser.add_argument("--measurement-plane", default="unspecified", help="人工测量平面，如 bottom_inner / bottom_outer / agar_surface / ruler")
    parser.add_argument("--dish-diameter-px", type=float, default=None, help="已知皿像素直径，跳过自动检测")
    parser.add_argument("--dish-center", default=None, help="皿中心 x,y（配合 --dish-diameter-px）")
    parser.add_argument("--scale-mm-per-px", type=float, default=None, help="已知 mm/px，优先于皿直径标定")
    parser.add_argument("--center", default=None, help="菌落中心 x,y")
    parser.add_argument("--colony-roi", default=None, help="菌落候选区域 x,y,w,h")
    parser.add_argument("--method", choices=["auto", "dark", "light", "vs", "texture"], default="auto", help="分割方法：auto/dark/light/vs/texture")
    parser.add_argument("--threshold", type=float, default=None, help="手动分割阈值（0-255）")
    parser.add_argument("--min-colony-area-px", type=float, default=None, help="菌落最小面积（像素）")
    parser.add_argument("--rays", type=int, default=12, help="射线数；默认 12 条得到最多 6 个直径")
    parser.add_argument("--ray-mode", choices=["paired", "radial"], default="paired", help="paired=对径相加，radial=每半径×2")
    parser.add_argument("--ray-offset-deg", type=float, default=0.0, help="射线起始角度")
    parser.add_argument("--mask", default=None, help="手动主菌落掩膜路径；提供后跳过自动分割")
    parser.add_argument("--contamination-mode", choices=["off", "fungal", "auto"], default="fungal", help="杂菌判定：off=本批无污染，fungal=仅明显真菌菌丝污染，auto=较宽松")
    parser.add_argument("--anchor-mode", choices=["plug", "center", "component"], default="plug", help="主菌落锚点：plug=中央菌饼，center=皿中心，component=自动")
    parser.add_argument("--boundary-engine", choices=["grabcut", "radial", "component"], default="grabcut", help="边界引擎：grabcut=菌饼中心种子+GrabCut，radial=径向边缘，component=连通域")
    parser.add_argument("--boundary-mode", choices=["hybrid", "radial", "component", "auto"], default="hybrid", help="radial 引擎内部的回退模式")
    parser.add_argument("--grabcut-max-size", type=int, default=900, help="GrabCut 处理的最大边长，默认 900")
    parser.add_argument("--grabcut-fg-ratio", type=float, default=0.40, help="GrabCut 可能前景半径/皿半径，默认 0.40")
    parser.add_argument("--grabcut-sure-fg-ratio", type=float, default=0.08, help="GrabCut 确定前景半径/皿半径，默认 0.08")
    parser.add_argument("--grabcut-iterations", type=int, default=5, help="GrabCut 迭代次数，默认 5")
    parser.add_argument("--radial-rays", type=int, default=72, help="径向边界射线数，默认 72")
    parser.add_argument("--radial-max-radius-ratio", type=float, default=0.85, help="径向搜索最大半径占皿半径比例，默认 0.85")
    parser.add_argument("--regularity-cv-threshold", type=float, default=0.0, help="规则菌落径向 CV 阈值；默认 0=关闭椭圆拟合，保留真实轮廓。设为 0.15 可恢复「平滑椭圆」旧行为")
    parser.add_argument("--main-colony-mode", choices=["auto", "center", "largest"], default="auto", help="主菌落选择：auto=中心偏大，center=最靠中心，largest=最大")
    parser.add_argument("--halo-mode", choices=["auto", "tight", "loose"], default="auto", help="外沿扩展模式：auto/tight/loose")
    parser.add_argument("--halo-low-percentile", type=float, default=25.0, help="菌丝外沿低阈值百分位，默认 25")
    parser.add_argument("--halo-texture-percentile", type=float, default=25.0, help="菌丝纹理阈值百分位，默认 35")
    parser.add_argument("--halo-search-ratio", type=float, default=0.12, help="外沿搜索半径相对主菌落半径比例，默认 0.08")
    parser.add_argument("--min-contaminant-area-fraction", type=float, default=0.004, help="杂菌候选最小面积占皿面积比例，默认 0.004")
    parser.add_argument("--core-score-percentile-mid", type=float, default=50.0, help="中阈值用途百分位，默认 50")
    parser.add_argument("--core-score-percentile-high", type=float, default=70.0, help="紧凑菌落高阈值百分位，默认 70")
    parser.add_argument("--edge-type", default="regular", help="regular 或 irregular，影响 CV 阈值")
    parser.add_argument("--cv-threshold", type=float, default=0.15, help="常规菌落 CV 阈值")
    parser.add_argument("--ecd-review-threshold", type=float, default=0.15, help="十字直径与等效圆直径（ECD）相对偏差复核阈值")
    parser.add_argument("--irregular-cv-threshold", type=float, default=0.25, help="不规则菌落 CV 阈值")
    return parser


def process_manifest(args, output_dir: Path):
    manifest_path = Path(args.manifest).expanduser().resolve()
    rows, headers = read_manifest(manifest_path)
    columns = {key: find_column(headers, aliases) for key, aliases in ALIASES.items()}
    if not columns.get("image"):
        raise ValueError("清单找不到图片列（image_path / 图片路径）")

    qa_dir = output_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    output_rows = []
    details = []

    for index, row in enumerate(rows, start=1):
        image_value = value_of(row, columns["image"])
        if not image_value:
            details.append({"row": index, "status": "skipped", "reason": "缺少 image_path"})
            continue
        image_path = Path(image_value).expanduser()
        if not image_path.is_absolute():
            image_path = (manifest_path.parent / image_path).resolve()
        if not image_path.exists():
            details.append({"row": index, "status": "error", "reason": f"图片不存在: {image_path}"})
            continue

        context = {}
        for key in ("experiment", "sample", "treatment", "replicate", "dish_mm", "reference_mm", "measurement_plane", "camera_angle", "scale_axis", "dish_px", "scale", "plug", "tip", "center", "roi", "rays", "method", "threshold", "min_area", "main_colony_mode", "anchor_mode", "boundary_engine", "boundary_mode", "radial_rays", "grabcut_max_size", "grabcut_fg_ratio", "grabcut_sure_fg_ratio", "grabcut_iterations", "halo_mode", "halo_low_percentile", "halo_texture_percentile", "halo_search_ratio", "min_contaminant_area_fraction", "mask", "contamination_mode", "edge", "notes"):
            context[key] = value_of(row, columns.get(key))
        row_args = argparse.Namespace(**vars(args))
        if context.get("rays"):
            row_rays = int(parse_float(context["rays"], args.rays))
            if row_rays >= 4:
                row_args.rays = row_rays
        if context.get("method"):
            row_args.method = context["method"]
        if context.get("threshold"):
            row_args.threshold = parse_float(context["threshold"], row_args.threshold)
        if context.get("min_area"):
            row_args.min_colony_area_px = parse_float(context["min_area"], row_args.min_colony_area_px)
        if context.get("main_colony_mode"):
            row_args.main_colony_mode = context["main_colony_mode"]
        if context.get("anchor_mode"):
            row_args.anchor_mode = context["anchor_mode"]
        if context.get("boundary_engine"):
            row_args.boundary_engine = context["boundary_engine"]
        if context.get("boundary_mode"):
            row_args.boundary_mode = context["boundary_mode"]
        for key, attr in [("grabcut_max_size","grabcut_max_size"),("grabcut_fg_ratio","grabcut_fg_ratio"),("grabcut_sure_fg_ratio","grabcut_sure_fg_ratio"),("grabcut_iterations","grabcut_iterations")]:
            if context.get(key):
                setattr(row_args, attr, parse_float(context[key], getattr(row_args, attr)))
        if context.get("radial_rays"):
            row_args.radial_rays = int(parse_float(context["radial_rays"], row_args.radial_rays))
        if context.get("halo_mode"):
            row_args.halo_mode = context["halo_mode"]
        if context.get("mask"):
            row_args.mask = context["mask"]
        if context.get("contamination_mode"):
            row_args.contamination_mode = context["contamination_mode"]
        if context.get("halo_low_percentile"):
            row_args.halo_low_percentile = parse_float(context["halo_low_percentile"], row_args.halo_low_percentile)
        if context.get("halo_texture_percentile"):
            row_args.halo_texture_percentile = parse_float(context["halo_texture_percentile"], row_args.halo_texture_percentile)
        if context.get("halo_search_ratio"):
            row_args.halo_search_ratio = parse_float(context["halo_search_ratio"], row_args.halo_search_ratio)
        if context.get("min_contaminant_area_fraction"):
            row_args.min_contaminant_area_fraction = parse_float(context["min_contaminant_area_fraction"], row_args.min_contaminant_area_fraction)

        try:
            result = measure_one(image_path, row_args, context)
        except Exception as exc:
            details.append({"row": index, "image_path": str(image_path), "status": "error", "reason": str(exc)})
            continue

        stem = re.sub(r"[^\w.-]+", "_", image_path.stem)[:80] or f"image_{index:04d}"
        annotated_path = qa_dir / f"{stem}_annotated.jpg"
        mask_path = qa_dir / f"{stem}_mask.png"
        imwrite_unicode(annotated_path, result["annotated_image"])
        imwrite_unicode(mask_path, result["mask"])

        output_row = OrderedDict()
        output_row["experiment_id"] = context.get("experiment") or "EXP"
        output_row["sample_id"] = context.get("sample") or stem
        output_row["treatment"] = context.get("treatment") or ""
        output_row["replicate"] = context.get("replicate") or ""
        output_row["image_path"] = str(image_path)
        output_row["unit"] = "mm"
        output_row["dish_diameter_mm"] = context.get("reference_mm") or context.get("dish_mm") or result["reference_diameter_mm"]
        output_row["reference_diameter_mm"] = result["reference_diameter_mm"]
        output_row["measurement_plane"] = result["measurement_plane"]
        output_row["camera_angle"] = result["camera_angle"]
        output_row["scale_axis"] = result["scale_axis"]
        output_row["dish_diameter_px"] = f"{result['dish_diameter_px']:.4f}"
        output_row["major_axis_px"] = f"{result['major_axis_px']:.4f}"
        output_row["minor_axis_px"] = f"{result['minor_axis_px']:.4f}"
        output_row["perspective_ratio"] = f"{result['perspective_ratio']:.4f}"
        output_row["scale_mm_per_px"] = f"{result['scale_mm_per_px']:.8f}"
        output_row["plug_diameter_mm"] = context.get("plug") or ""
        output_row["tip_color"] = context.get("tip") or ""
        output_row["edge_type"] = context.get("edge") or result["edge_type"]
        output_row["main_colony_mode"] = result["main_colony_mode"]
        output_row["main_source"] = result["main_source"]
        output_row["anchor_mode"] = result.get("anchor_mode", "")
        output_row["plug_method"] = result.get("plug_method", "")
        output_row["boundary_engine"] = result.get("boundary_engine", "")
        output_row["boundary_method"] = result.get("boundary_method", "")
        output_row["boundary_decision"] = result.get("boundary_decision", "")
        output_row["radial_cv"] = "" if result.get("radial_cv") is None else f"{result['radial_cv']:.4f}"
        output_row["contamination_mode"] = result.get("contamination_mode", "")
        output_row["main_whiteness"] = f"{result['main_whiteness']:.4f}"
        output_row["n_colony_candidates"] = result["n_colony_candidates"]
        output_row["n_contaminants"] = result["n_contaminants"]
        output_row["ambiguous_main_colony"] = "true" if result["ambiguous_main_colony"] else "false"
        output_row["auto_needs_review"] = "true" if result["needs_review"] else "false"
        output_row["auto_review_reasons"] = result["review_reasons"]
        output_row["equivalent_diameter_mm"] = f"{result['equivalent_diameter_mm']:.4f}"
        output_row["feret_diameter_mm"] = f"{result['feret_diameter_mm']:.4f}"
        output_row["cross_vs_ecd_deviation"] = "" if result["cross_vs_ecd_deviation"] is None else f"{result['cross_vs_ecd_deviation']:.4f}"
        for idx, value in enumerate(result["diameter_mm"], start=1):
            output_row[f"m{idx}"] = f"{value:.4f}" if value > 0 else ""
        output_rows.append(output_row)
        details.append(
            {
                "row": index,
                "status": "ok",
                "image_path": str(image_path),
                "annotated_image": str(annotated_path),
                "mask": str(mask_path),
                "mean_diameter_mm": result["mean_diameter_mm"],
                "sd_mm": result["sd_mm"],
                "cv": result["cv"],
                "dish_confidence": result["dish_confidence"],
                "colony_confidence": result["colony_confidence"],
                "needs_review": result["needs_review"],
                "review_reasons": result["review_reasons"],
            }
        )

    fields = [
        "experiment_id", "sample_id", "treatment", "replicate", "image_path", "unit",
        "dish_diameter_mm", "reference_diameter_mm", "measurement_plane", "camera_angle", "scale_axis",
        "dish_diameter_px", "major_axis_px", "minor_axis_px", "perspective_ratio", "scale_mm_per_px", "plug_diameter_mm",
        "tip_color", "edge_type", "main_colony_mode", "main_source", "main_whiteness", "anchor_mode", "plug_method", "boundary_engine", "boundary_method", "boundary_decision", "radial_cv", "contamination_mode", "n_colony_candidates", "n_contaminants",
        "ambiguous_main_colony", "auto_needs_review", "auto_review_reasons",
        "equivalent_diameter_mm", "feret_diameter_mm", "cross_vs_ecd_deviation",
    ] + [f"m{i}" for i in range(1, 7)]
    measurements_path = output_dir / "measurements_auto.csv"
    with measurements_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output_rows)

    details_path = output_dir / "image_measurements.json"
    details_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "manifest": str(manifest_path),
                "measurements_csv": str(measurements_path),
                "images": details,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"已处理 {len(output_rows)} / {len(rows)} 张图片")
    print(f"测量 CSV: {measurements_path}")
    print(f"图像详情: {details_path}")
    return 0


def process_single(args, output_dir: Path):
    image_path = Path(args.image).expanduser().resolve()
    if not image_path.exists():
        raise ValueError(f"图片不存在: {image_path}")
    result = measure_one(image_path, args, {})
    qa_dir = output_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    annotated_path = qa_dir / f"{image_path.stem}_annotated.jpg"
    mask_path = qa_dir / f"{image_path.stem}_mask.png"
    imwrite_unicode(annotated_path, result.pop("annotated_image"))
    imwrite_unicode(mask_path, result.pop("mask"))

    fields = ["image_path", "mean_diameter_mm", "sd_mm", "cv", "equivalent_diameter_mm", "feret_diameter_mm", "cross_vs_ecd_deviation", "n_contaminants", "ambiguous_main_colony", "needs_review", "review_reasons"]
    fields += [f"m{i}_mm" for i in range(1, 7)]
    row = OrderedDict()
    row["image_path"] = str(image_path)
    row["mean_diameter_mm"] = f"{result['mean_diameter_mm']:.4f}"
    row["sd_mm"] = f"{result['sd_mm']:.4f}"
    row["cv"] = "" if result["cv"] is None else f"{result['cv']:.4f}"
    row["equivalent_diameter_mm"] = f"{result['equivalent_diameter_mm']:.4f}"
    row["feret_diameter_mm"] = f"{result['feret_diameter_mm']:.4f}"
    row["cross_vs_ecd_deviation"] = "" if result["cross_vs_ecd_deviation"] is None else f"{result['cross_vs_ecd_deviation']:.4f}"
    row["n_contaminants"] = result["n_contaminants"]
    row["ambiguous_main_colony"] = "true" if result["ambiguous_main_colony"] else "false"
    row["needs_review"] = "true" if result["needs_review"] else "false"
    row["review_reasons"] = result["review_reasons"]
    for idx, value in enumerate(result["diameter_mm"], start=1):
        row[f"m{idx}_mm"] = f"{value:.4f}" if value > 0 else ""

    csv_path = output_dir / "measurement.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(row)

    json_path = output_dir / "measurement.json"
    result["annotated_image"] = str(annotated_path)
    result["mask"] = str(mask_path)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"平均直径: {result['mean_diameter_mm']:.3f} mm")
    print(f"测量 CSV: {csv_path}")
    print(f"标注图: {annotated_path}")
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if bool(args.image) == bool(args.manifest):
        print("错误：必须且只能指定 --image 或 --manifest 之一", file=sys.stderr)
        return 2
    try:
        if args.manifest:
            return process_manifest(args, output_dir)
        return process_single(args, output_dir)
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
