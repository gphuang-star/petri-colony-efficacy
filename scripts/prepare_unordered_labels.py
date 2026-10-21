#!/usr/bin/env python3
"""Build unordered per-plate labels from a wide manual measurement table.

A treatment may have k images and k pairs of manual measurements, but the
pair-to-image mapping may be unknown.  This script keeps that uncertainty:
each row stores one pair mean plus the set of images for the treatment, with
mapping_status=unknown.  It also writes an image manifest with set_id.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
from collections import OrderedDict, defaultdict
from pathlib import Path


def read_rows(path: Path):
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle)
                return list(reader), list(reader.fieldnames or [])
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法解码 CSV: {path}")


def norm(value: str) -> str:
    value = str(value or "").strip().lower().replace("\ufeff", "")
    value = re.sub(r"[\s\-/（）()]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_")


def find_col(headers, aliases):
    mapping = {norm(h): h for h in headers}
    for alias in aliases:
        if norm(alias) in mapping:
            return mapping[norm(alias)]
    return None


def parse_float(value):
    text = str(value or "").strip().replace("，", ".")
    if text == "":
        return None
    text = re.sub(r"[^\d.eE+\-]", "", text)
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def detect_value_columns(headers):
    patterns = [
        re.compile(r"^(?:m|meas|measurement|d|diam|diameter)_?(\d+)$"),
        re.compile(r"^(?:直径|测量)_?(\d+)$"),
    ]
    found = []
    for header in headers:
        for pattern in patterns:
            match = pattern.match(norm(header))
            if match:
                found.append((int(match.group(1)), header))
                break
    if found:
        return [h for _, h in sorted(found)]
    # fall back to single-letter columns B..G
    letters = ["b", "c", "d", "e", "f", "g", "h", "i", "j"]
    present = [norm(h) for h in headers]
    return [headers[present.index(letter)] for letter in letters if letter in present]


def write_rows(path: Path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_parser():
    parser = argparse.ArgumentParser(description="把每个处理的宽表测量值转换为无序的每皿标签集合。")
    parser.add_argument("--manual-csv", required=True, help="人工测量宽表 CSV")
    parser.add_argument("--manifest", required=True, help="图片清单 CSV（每张图片一行）")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--treatment-col", default="treatment", help="处理列名，默认 treatment")
    parser.add_argument("--experiment-col", default="experiment_id", help="实验列名，默认 experiment_id")
    parser.add_argument("--image-col", default="image_path", help="图片列名，默认 image_path")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    manual_rows, manual_headers = read_rows(Path(args.manual_csv).expanduser().resolve())
    manifest_rows, manifest_headers = read_rows(Path(args.manifest).expanduser().resolve())
    value_cols = detect_value_columns(manual_headers)
    if not value_cols:
        raise SystemExit("人工表找不到 m1..mN / B..G 测量列")
    treatment_col = find_col(manual_headers, [args.treatment_col, "treatment", "处理", "处理名称"])
    experiment_col = find_col(manual_headers, [args.experiment_col, "experiment_id", "实验编号", "试验编号"])
    if not treatment_col:
        raise SystemExit("人工表找不到处理列")

    manifest_treatment = find_col(manifest_headers, [args.treatment_col, "treatment", "处理", "处理名称"])
    manifest_experiment = find_col(manifest_headers, [args.experiment_col, "experiment_id", "实验编号", "试验编号"])
    image_col = find_col(manifest_headers, [args.image_col, "image_path", "image", "图片", "图片路径"])
    if not manifest_treatment or not image_col:
        raise SystemExit("图片清单找不到处理列或图片列")

    images_by_set = defaultdict(list)
    for row in manifest_rows:
        experiment = str(row.get(manifest_experiment, "") or "EXP").strip()
        treatment = str(row.get(manifest_treatment, "") or "").strip()
        image_path = str(row.get(image_col, "") or "").strip()
        if treatment and image_path:
            images_by_set[(experiment, treatment)].append(image_path)

    labels = []
    manifest_out = []
    warnings = []
    for row in manual_rows:
        experiment = str(row.get(experiment_col, "") or "EXP").strip()
        treatment = str(row.get(treatment_col, "") or "").strip()
        if not treatment:
            continue
        values = [parse_float(row.get(col)) for col in value_cols]
        values = [value for value in values if value is not None and value > 0]
        pairs = [(values[i], values[i + 1]) for i in range(0, len(values) - 1, 2)]
        images = images_by_set.get((experiment, treatment), [])
        if len(images) != len(pairs):
            warnings.append(f"{experiment}/{treatment}: {len(images)} 张图片 vs {len(pairs)} 对人工值")
        for index, pair in enumerate(pairs, start=1):
            labels.append(OrderedDict([
                ("experiment_id", experiment),
                ("treatment", treatment),
                ("pair_id", f"P{index}"),
                ("pair_mean_mm", round(sum(pair) / len(pair), 4)),
                ("pair_m1_mm", pair[0]),
                ("pair_m2_mm", pair[1]),
                ("image_count", len(images)),
                ("images", ";".join(images)),
                ("mapping_status", "unknown"),
            ]))
        for image_path in images:
            manifest_out.append(OrderedDict([
                ("experiment_id", experiment),
                ("treatment", treatment),
                ("set_id", f"{experiment}:{treatment}"),
                ("image_path", image_path),
            ]))

    write_rows(out_dir / "unordered_labels.csv", labels, list(labels[0].keys()) if labels else ["experiment_id", "treatment", "pair_id", "pair_mean_mm"])
    write_rows(out_dir / "image_manifest_with_set_id.csv", manifest_out, ["experiment_id", "treatment", "set_id", "image_path"])
    (out_dir / "warnings.txt").write_text("\n".join(warnings) + ("\n" if warnings else ""), encoding="utf-8")
    print(f"无序标签: {out_dir / 'unordered_labels.csv'}")
    print(f"图片集合清单: {out_dir / 'image_manifest_with_set_id.csv'}")
    for warning in warnings:
        print("警告:", warning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
