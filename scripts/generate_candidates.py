#!/usr/bin/env python3
"""Generate per-image diameter candidates from several segmentation methods.

This helper is intended for accumulating training data: it runs the same
manifest through multiple methods and writes one row per image with each
method's diameter candidate.  An optional labels CSV can be supplied to
compute errors immediately.

Example:
    python generate_candidates.py \
        --manifest plate_manifest.csv \
        --output-dir candidates \
        --methods auto,light,vs,texture \
        --rays 4 --dish-diameter-mm 86
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path


METHODS_DEFAULT = "auto,light,vs,texture"


def read_rows(path: Path):
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle)
                return list(reader), list(reader.fieldnames or [])
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法解码 CSV: {path}")


def write_rows(path: Path, rows: list[dict], preferred_fields=None):
    if not rows:
        return
    fields = preferred_fields or list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def numeric_mean(values):
    valid = []
    for value in values:
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            continue
        if number > 0:
            valid.append(number)
    return (sum(valid) / len(valid), len(valid)) if valid else (None, 0)


def build_parser():
    parser = argparse.ArgumentParser(description="为每张图片生成多种分割方法的直径候选，用于人工比对和后续模型训练。")
    parser.add_argument("--manifest", required=True, help="图片清单 CSV")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--methods", default=METHODS_DEFAULT, help=f"逗号分隔的方法，默认 {METHODS_DEFAULT}")
    parser.add_argument("--rays", type=int, default=4, help="每张图片的射线数；4=两条垂直直径")
    parser.add_argument("--dish-diameter-mm", type=float, default=86.0, help="培养皿实际直径，默认 86 mm")
    parser.add_argument("--labels", default=None, help="可选人工标签 CSV（sample_id, manual_diameter_mm）")
    parser.add_argument("--python-exe", default=sys.executable, help="调用 measure_colony_image.py 的 Python")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    script_dir = Path(__file__).resolve().parent
    measure_script = script_dir / "measure_colony_image.py"
    if not measure_script.exists():
        raise SystemExit(f"缺少脚本: {measure_script}")

    manifest = Path(args.manifest).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows, fields = read_rows(manifest)
    if not rows:
        raise SystemExit("清单没有有效数据行")
    if "image_path" not in fields:
        raise SystemExit("清单缺少 image_path 列")

    methods = [m.strip() for m in str(args.methods).split(",") if m.strip()]
    all_results: dict[str, dict] = {}
    for method in methods:
        method_dir = output_dir / f"method_{method}"
        method_dir.mkdir(parents=True, exist_ok=True)
        method_manifest = output_dir / f"manifest_{method}.csv"
        write_rows(method_manifest, [{**row, "method": method} for row in rows], fields + ([] if "method" in fields else ["method"]))
        cmd = [
            str(args.python_exe), str(measure_script),
            "--manifest", str(method_manifest),
            "--output-dir", str(method_dir),
            "--rays", str(args.rays),
            "--dish-diameter-mm", str(args.dish_diameter_mm),
            "--method", method,
        ]
        print(f"运行方法 {method} ...")
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            print(f"方法 {method} 失败：{proc.stderr.strip() or proc.stdout.strip()}")
            continue
        measurement_csv = method_dir / "measurements_auto.csv"
        if not measurement_csv.exists():
            print(f"方法 {method} 没有生成 measurements_auto.csv")
            continue
        measured_rows, _ = read_rows(measurement_csv)
        for row in measured_rows:
            sample_id = row.get("sample_id") or row.get("image_path") or ""
            diameter, count = numeric_mean([row.get(f"m{i}") for i in range(1, 7)])
            all_results.setdefault(sample_id, {})[method] = {
                "diameter_mm": diameter,
                "n_measurements": count,
                "equivalent_diameter_mm": row.get("equivalent_diameter_mm", ""),
                "cross_vs_ecd_deviation": row.get("cross_vs_ecd_deviation", ""),
                "needs_review": row.get("auto_needs_review", ""),
                "review_reasons": row.get("auto_review_reasons", ""),
            }

    labels = {}
    if args.labels:
        label_rows, _ = read_rows(Path(args.labels).expanduser().resolve())
        for row in label_rows:
            sample_id = row.get("sample_id") or row.get("image_path") or ""
            labels[sample_id] = row.get("manual_diameter_mm") or row.get("manual_diameter") or ""

    output_rows = []
    for row in rows:
        sample_id = row.get("sample_id") or row.get("image_path") or ""
        output = {
            "experiment_id": row.get("experiment_id", ""),
            "sample_id": sample_id,
            "treatment": row.get("treatment", ""),
            "replicate": row.get("replicate", ""),
            "image_path": row.get("image_path", ""),
            "manual_diameter_mm": labels.get(sample_id, ""),
        }
        for method in methods:
            result = all_results.get(sample_id, {}).get(method, {})
            output[f"{method}_diameter_mm"] = round(result.get("diameter_mm"), 4) if result.get("diameter_mm") is not None else ""
            output[f"{method}_n_measurements"] = result.get("n_measurements", "")
            output[f"{method}_ecd_mm"] = result.get("equivalent_diameter_mm", "")
            output[f"{method}_review"] = result.get("review_reasons", "")
        output_rows.append(output)

    candidates_path = output_dir / "candidate_diameters.csv"
    write_rows(candidates_path, output_rows)

    summary_rows = []
    for method in methods:
        errors = []
        for row in output_rows:
            try:
                manual = float(row.get("manual_diameter_mm"))
                candidate = float(row.get(f"{method}_diameter_mm"))
            except (TypeError, ValueError):
                continue
            errors.append(abs(candidate - manual))
        summary_rows.append({
            "method": method,
            "n_scored": len(errors),
            "mae_mm": round(sum(errors) / len(errors), 4) if errors else "",
            "max_abs_error_mm": round(max(errors), 4) if errors else "",
        })
    write_rows(output_dir / "method_summary.csv", summary_rows)

    print(f"候选表: {candidates_path}")
    print(f"方法汇总: {output_dir / 'method_summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
