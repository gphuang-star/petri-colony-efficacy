#!/usr/bin/env python3
"""Evaluate per-image predictions against unordered per-plate labels.

When the pair-to-image mapping is unknown, per-image MAE is undefined.  This
script therefore reports:

- treatment-mean error (order independent);
- sorted-set MAE (sort predictions and labels, then compare);
- optimal-assignment MAE (best one-to-one matching);
- an image -> pair_id assignment suggestion for optional human confirmation.

Predictions CSV must contain one row per image with a predicted diameter.
Labels CSV is the output of prepare_unordered_labels.py.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from itertools import permutations
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


def mean(values):
    return sum(values) / len(values) if values else None


def rankdata(values):
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
            end += 1
        average_rank = (index + end) / 2.0 + 1.0
        for position in range(index, end + 1):
            ranks[order[position]] = average_rank
        index = end + 1
    return ranks


def pearson(left, right):
    if len(left) != len(right) or len(left) < 2:
        return None
    mean_left = mean(left)
    mean_right = mean(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    denominator = math.sqrt(sum((a - mean_left) ** 2 for a in left) * sum((b - mean_right) ** 2 for b in right))
    return numerator / denominator if denominator > 0 else None


def optimal_assignment(predictions, labels):
    k = len(predictions)
    if k == 0 or len(labels) != k:
        return None, None
    if k > 7:
        order = sorted(range(k), key=lambda index: predictions[index])
        sorted_labels = sorted(range(k), key=lambda index: labels[index])
        mapping = {order[i]: sorted_labels[i] for i in range(k)}
        error = mean([abs(predictions[i] - labels[mapping[i]]) for i in mapping])
        return error, mapping
    best_error = None
    best_mapping = None
    for permutation in permutations(range(k)):
        error = mean([abs(predictions[i] - labels[permutation[i]]) for i in range(k)])
        if best_error is None or error < best_error:
            best_error = error
            best_mapping = {i: permutation[i] for i in range(k)}
    return best_error, best_mapping


def write_rows(path: Path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_parser():
    parser = argparse.ArgumentParser(description="用无序的每皿标签评价逐图预测，并给出图片-标签匹配建议。")
    parser.add_argument("--predictions", required=True, help="逐图预测 CSV")
    parser.add_argument("--labels", required=True, help="prepare_unordered_labels.py 生成的无序标签 CSV")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--prediction-col", default="predicted_diameter_mm", help="预测直径列名")
    parser.add_argument("--image-col", default="image_path", help="图片路径列名")
    parser.add_argument("--treatment-col", default="treatment", help="处理列名")
    parser.add_argument("--experiment-col", default="experiment_id", help="实验列名")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    pred_rows, pred_headers = read_rows(Path(args.predictions).expanduser().resolve())
    label_rows, label_headers = read_rows(Path(args.labels).expanduser().resolve())

    pred_col = find_col(pred_headers, [args.prediction_col, "predicted_diameter_mm", "diameter_mm", "mean_diameter_mm", "prediction"])
    pred_image = find_col(pred_headers, [args.image_col, "image_path", "image", "图片", "图片路径"])
    pred_treatment = find_col(pred_headers, [args.treatment_col, "treatment", "处理", "处理名称"])
    pred_experiment = find_col(pred_headers, [args.experiment_col, "experiment_id", "实验编号", "试验编号"])
    label_treatment = find_col(label_headers, [args.treatment_col, "treatment", "处理", "处理名称"])
    label_experiment = find_col(label_headers, [args.experiment_col, "experiment_id", "实验编号", "试验编号"])
    label_pair = find_col(label_headers, ["pair_id", "pair", "标签编号"])
    label_value = find_col(label_headers, ["pair_mean_mm", "manual_diameter_mm", "label_mm", "值"])
    if not pred_col or not pred_treatment or not label_treatment or not label_value:
        raise SystemExit("预测或标签 CSV 缺少必要列")

    pred_groups = defaultdict(list)
    for row in pred_rows:
        experiment = str(row.get(pred_experiment, "") or "EXP").strip()
        treatment = str(row.get(pred_treatment, "") or "").strip()
        value = parse_float(row.get(pred_col))
        image_path = str(row.get(pred_image, "") or "").strip()
        if treatment and value is not None:
            pred_groups[(experiment, treatment)].append({"image_path": image_path, "value": value})

    label_groups = defaultdict(list)
    for row in label_rows:
        experiment = str(row.get(label_experiment, "") or "EXP").strip()
        treatment = str(row.get(label_treatment, "") or "").strip()
        value = parse_float(row.get(label_value))
        pair_id = str(row.get(label_pair, "") or "").strip()
        if treatment and value is not None:
            label_groups[(experiment, treatment)].append({"pair_id": pair_id, "value": value})

    evaluation = []
    assignments = []
    for key in sorted(label_groups):
        experiment, treatment = key
        predictions = pred_groups.get(key, [])
        labels = label_groups.get(key, [])
        if not predictions:
            continue
        pred_values = [item["value"] for item in predictions]
        label_values = [item["value"] for item in labels]
        mean_pred = mean(pred_values)
        mean_label = mean(label_values)
        sorted_mae = None
        if len(pred_values) == len(label_values):
            sorted_mae = mean([abs(a - b) for a, b in zip(sorted(pred_values), sorted(label_values))])
        assignment_error, mapping = optimal_assignment(pred_values, label_values)
        correlation = pearson(rankdata(pred_values), rankdata(label_values)) if len(pred_values) == len(label_values) else None
        evaluation.append({
            "experiment_id": experiment,
            "treatment": treatment,
            "n_images": len(predictions),
            "n_labels": len(labels),
            "mean_predicted_mm": round(mean_pred, 4) if mean_pred is not None else "",
            "mean_manual_mm": round(mean_label, 4) if mean_label is not None else "",
            "mean_error_mm": round(abs(mean_pred - mean_label), 4) if (mean_pred is not None and mean_label is not None) else "",
            "sorted_mae_mm": round(sorted_mae, 4) if sorted_mae is not None else "",
            "optimal_assignment_mae_mm": round(assignment_error, 4) if assignment_error is not None else "",
            "rank_correlation": round(correlation, 4) if correlation is not None else "",
        })
        if mapping:
            for index, item in enumerate(predictions):
                label_index = mapping.get(index)
                if label_index is None or label_index >= len(labels):
                    continue
                label = labels[label_index]
                assignments.append({
                    "experiment_id": experiment,
                    "treatment": treatment,
                    "image_path": item["image_path"],
                    "predicted_mm": round(item["value"], 4),
                    "suggested_pair_id": label["pair_id"],
                    "pair_mean_mm": round(label["value"], 4),
                    "abs_error_mm": round(abs(item["value"] - label["value"]), 4),
                    "assignment_cost_mm": round(assignment_error, 4) if assignment_error is not None else "",
                    "mapping_status": "suggested_requires_confirmation",
                })

    write_rows(out_dir / "unordered_evaluation.csv", evaluation, [
        "experiment_id", "treatment", "n_images", "n_labels", "mean_predicted_mm", "mean_manual_mm",
        "mean_error_mm", "sorted_mae_mm", "optimal_assignment_mae_mm", "rank_correlation",
    ])
    write_rows(out_dir / "assignment_suggestions.csv", assignments, [
        "experiment_id", "treatment", "image_path", "predicted_mm", "suggested_pair_id", "pair_mean_mm",
        "abs_error_mm", "assignment_cost_mm", "mapping_status",
    ])
    summary = {
        "n_treatments": len(evaluation),
        "mean_error_mm": round(mean([row["mean_error_mm"] for row in evaluation if row["mean_error_mm"] != ""]), 4) if evaluation else "",
        "mean_sorted_mae_mm": round(mean([row["sorted_mae_mm"] for row in evaluation if row["sorted_mae_mm"] != ""]), 4) if evaluation else "",
        "mean_optimal_assignment_mae_mm": round(mean([row["optimal_assignment_mae_mm"] for row in evaluation if row["optimal_assignment_mae_mm"] != ""]), 4) if evaluation else "",
    }
    (out_dir / "evaluation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"无序评价: {out_dir / 'unordered_evaluation.csv'}")
    print(f"匹配建议: {out_dir / 'assignment_suggestions.csv'}")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
