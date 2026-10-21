#!/usr/bin/env python3
"""Compute colony mean diameters and control efficacy from cross measurements.

Input is a CSV where one row is one colony (usually one Petri dish).
Each row may contain up to six measurement columns (m1..m6, d1..d6,
diameter_1..diameter_6, or radius columns r1..r6).  Values are converted to
mm, averaged per colony, averaged per treatment, and finally expressed as
efficacy relative to CK:

    efficacy(%) = (CK_mean - treatment_mean) / (CK_mean - plug_diameter) * 100

The script is intentionally dependency-free (standard library only).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ALIASES = {
    "experiment": ["experiment_id", "experiment", "exp_id", "exp", "试验编号", "实验编号", "试验", "实验"],
    "treatment": ["treatment", "treatment_name", "treat", "处理", "处理名称", "药剂", "处理编号"],
    "replicate": ["replicate", "rep", "replicate_id", "repeat", "重复", "重复编号"],
    "sample": ["sample_id", "colony_id", "sample", "id", "菌落编号", "样本编号", "编号"],
    "is_ck": ["is_ck", "isc k", "ck_flag", "is_control", "control", "对照", "是否对照"],
    "unit": ["unit", "units", "单位"],
    "plug": ["plug_diameter_mm", "plug_diameter", "plug_mm", "agar_plug_mm", "菌饼直径", "菌饼直径mm"],
    "tip": ["tip_color", "tip", "枪头", "枪头颜色"],
    "dish_px": ["dish_diameter_px", "plate_diameter_px", "petri_diameter_px", "培养皿像素直径", "皿直径像素"],
    "dish_mm": ["dish_diameter_mm", "plate_diameter_mm", "petri_diameter_mm", "培养皿直径", "皿直径mm"],
    "scale": ["scale_mm_per_px", "mm_per_px", "pixel_size_mm", "标定", "像素比例"],
    "edge": ["edge_type", "colony_shape", "shape", "边缘", "边缘类型", "菌落形状"],
    "image": ["image_path", "image", "photo", "photo_path", "图片", "图片路径"],
    "notes": ["notes", "note", "remark", "remarks", "备注"],
    "needs_review": ["needs_review", "auto_needs_review", "review", "需复核"],
    "review_reasons": ["review_reasons", "auto_review_reasons", "review_reason", "复核原因"],
    "ecd": ["equivalent_diameter_mm", "ecd_mm", "area_diameter_mm", "等效圆直径"],
    "single_diameter_mm": [
        "diameter_mm", "colony_diameter_mm", "mean_diameter_mm", "d_mm",
        "菌落直径", "菌落直径mm", "直径", "直径mm", "平均直径",
    ],
    "single_diameter_px": [
        "diameter_px", "colony_diameter_px", "mean_diameter_px",
        "菌落直径像素", "直径像素",
    ],
}


def norm_header(value: str) -> str:
    value = str(value or "").strip().lower()
    value = value.replace("\ufeff", "")
    value = re.sub(r"[\s\-/（）()]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def read_csv_rows(path: Path) -> tuple[list[OrderedDict], list[str], str]:
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames:
                    raise ValueError(f"CSV 没有表头: {path}")
                headers = [str(h or "").strip() for h in reader.fieldnames]
                rows: list[OrderedDict] = []
                for raw in reader:
                    row: OrderedDict = OrderedDict()
                    for key, value in raw.items():
                        row[str(key or "").strip()] = "" if value is None else str(value).strip()
                    if any(str(v).strip() for v in row.values()):
                        rows.append(row)
                return rows, headers, encoding
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    raise UnicodeDecodeError("csv", b"", 0, 1, f"无法解码 CSV（尝试 utf-8/gb18030）: {last_error}")


def find_column(headers: list[str], aliases: list[str]) -> str | None:
    normalized = {norm_header(h): h for h in headers}
    for alias in aliases:
        key = norm_header(alias)
        if key in normalized:
            return normalized[key]
    return None


def value_of(row: dict, column: str | None) -> str:
    if not column:
        return ""
    return str(row.get(column, "") or "").strip()


def parse_float(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"na", "n/a", "nan", "none", "null", "-", "--"}:
        return None
    text = text.replace("，", ".")
    text = re.sub(r"[^\d.eE+\-]", "", text)
    if text in {"", "+", "-", ".", "+.", "-."}:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    if not math.isfinite(result):
        return None
    return result


def truthy(value) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "是", "ck", "control", "对照", "√"}


def mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def sample_sd(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return statistics.stdev(values)


def round_or_none(value, digits=4):
    if value is None:
        return ""
    return round(float(value), digits)


def detect_measurement_columns(headers: list[str]) -> tuple[str | None, list[str]]:
    patterns = [
        (re.compile(r"^(?:m|meas|measurement|d|diam|diameter)_?(\d+)$"), "diameter"),
        (re.compile(r"^(?:直径|测量)_?(\d+)$"), "diameter"),
        (re.compile(r"^(?:r|radius|半径)_?(\d+)$"), "radius"),
    ]
    found: dict[str, list[tuple[int, str]]] = {"diameter": [], "radius": []}
    for header in headers:
        key = norm_header(header)
        for pattern, kind in patterns:
            match = pattern.match(key)
            if match:
                found[kind].append((int(match.group(1)), header))
                break
    if found["diameter"]:
        return "diameter", [h for _, h in sorted(found["diameter"])]
    if found["radius"]:
        return "radius", [h for _, h in sorted(found["radius"])]
    return None, []


def resolve_unit_factor(row: dict, columns: dict, args, measurement_hint: str, warnings: list[str]) -> tuple[float | None, str]:
    unit = value_of(row, columns.get("unit")).lower()
    scale = parse_float(value_of(row, columns.get("scale")))
    dish_px = parse_float(value_of(row, columns.get("dish_px")))
    dish_mm = parse_float(value_of(row, columns.get("dish_mm"))) or args.dish_diameter_mm

    # Explicit mm/cm values must not be rescaled merely because calibration
    # metadata columns are present (the auto-measurement CSV carries both).
    if unit in {"mm", "毫米", "公厘"}:
        return 1.0, "mm"
    if unit in {"cm", "厘米"}:
        return 10.0, "cm_to_mm"

    if unit in {"px", "pixel", "pixels", "像素"} or measurement_hint == "px":
        if scale and scale > 0:
            return scale, "scale_mm_per_px"
        if dish_px and dish_px > 0:
            return dish_mm / dish_px, "dish_calibration"
        warnings.append("测量值为 px 但缺少 scale_mm_per_px 或 dish_diameter_px，无法换算为 mm")
        return None, "missing_pixel_calibration"

    if unit == "":
        return 1.0, "assumed_mm"
    warnings.append(f"无法识别的单位 {unit!r}，按 mm 处理")
    return 1.0, "assumed_mm"


def tip_plug_value(tip_color: str, args) -> float | None:
    key = str(tip_color or "").strip().lower()
    if key in {"yellow", "黄", "黄色", "黄枪头", "yellow_tip"}:
        return args.yellow_plug_mm
    if key in {"blue", "蓝", "蓝色", "蓝枪头", "blue_tip"}:
        return args.blue_plug_mm
    return None


def mode_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    counts = Counter(round(float(v), 6) for v in values)
    top = counts.most_common()
    best_count = top[0][1]
    tied = [v for v, count in top if count == best_count]
    return max(tied) if len(tied) > 1 else top[0][0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="由菌落十字交叉测量值计算菌落直径、处理均值和相对 CK 的防效。"
    )
    parser.add_argument("--input", required=True, help="输入测量 CSV")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--dish-diameter-mm", type=float, default=86.0, help="培养皿实际直径，默认 86 mm")
    parser.add_argument("--plug-diameter-mm", type=float, default=None, help="实验级菌饼直径；不传则优先从行内 plug/tip 推断，最后默认 6 mm")
    parser.add_argument("--formula-mode", choices=["plug_corrected", "total_diameter"], default="plug_corrected", help="plug_corrected=(CK-处理)/(CK-菌饼)，默认；total_diameter=(CK-处理)/CK，已发表文献常用")
    parser.add_argument("--yellow-plug-mm", type=float, default=6.0, help="黄色枪头菌饼直径，默认 6 mm")
    parser.add_argument("--blue-plug-mm", type=float, default=8.0, help="蓝色枪头菌饼直径，默认 8 mm")
    parser.add_argument("--ck-names", default="CK,空白对照,对照,Control", help="逗号分隔的 CK 处理名")
    parser.add_argument("--expected-measurements", type=int, default=2, help="每个菌落/每张图片期望测量值个数，默认 2（十字法两条垂直直径）")
    parser.add_argument("--expected-total-measurements", type=int, default=6, help="每个处理期望测量值总数，默认 6（3 张图片 × 2 个直径）")
    parser.add_argument("--min-measurements", type=int, default=1, help="每个菌落最少测量值个数，默认 1")
    parser.add_argument("--expected-replicates", type=int, default=3, help="每个处理期望重复数，默认 3")
    parser.add_argument("--cv-threshold", type=float, default=0.15, help="规则菌落 CV 复核阈值，默认 0.15")
    parser.add_argument("--irregular-cv-threshold", type=float, default=0.25, help="不规则菌落 CV 复核阈值，默认 0.25")
    parser.add_argument("--ecd-review-threshold", type=float, default=0.15, help="十字直径与等效圆直径（ECD）相对偏差复核阈值，默认 0.15")
    parser.add_argument("--fail-on-review", action="store_true", help="存在 needs_review 时返回非零退出码")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    log_lines: list[str] = []
    warnings: list[str] = []

    def log(message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        log_lines.append(line)
        print(line)

    if not input_path.exists():
        print(f"错误：输入文件不存在: {input_path}", file=sys.stderr)
        return 2

    rows, headers, encoding = read_csv_rows(input_path)
    log(f"读取 {len(rows)} 行，编码 {encoding}，文件 {input_path}")
    if not rows:
        print("错误：CSV 没有有效数据行", file=sys.stderr)
        return 2

    columns = {key: find_column(headers, aliases) for key, aliases in ALIASES.items()}
    measurement_kind, measurement_columns = detect_measurement_columns(headers)

    if not columns.get("treatment"):
        print("错误：找不到处理列（treatment / 处理）", file=sys.stderr)
        return 2
    if measurement_kind is None and not columns.get("single_diameter_mm") and not columns.get("single_diameter_px"):
        print("错误：找不到测量列（m1..m6 / diameter_1..diameter_6 / 直径 / radius 等）", file=sys.stderr)
        return 2

    if measurement_kind:
        hint = "px" if any("px" in norm_header(c) for c in measurement_columns) else ""
        log(f"测量列：{measurement_kind}，共 {len(measurement_columns)} 列 -> {', '.join(measurement_columns)}")
    else:
        log("测量列：单列直径")

    ck_names = {norm_header(x) for x in str(args.ck_names).split(",") if x.strip()}
    records: list[dict] = []
    skipped_rows = 0
    conversion_counts: Counter = Counter()

    for index, row in enumerate(rows, start=1):
        treatment = value_of(row, columns["treatment"])
        if not treatment:
            skipped_rows += 1
            warnings.append(f"第 {index} 行缺少处理名，已跳过")
            continue

        experiment = value_of(row, columns.get("experiment")) or "EXP"
        sample_id = value_of(row, columns.get("sample")) or f"{experiment}-{index:04d}"
        replicate = value_of(row, columns.get("replicate")) or ""
        image_path = value_of(row, columns.get("image"))
        edge_type = value_of(row, columns.get("edge")) or "regular"
        tip_color = value_of(row, columns.get("tip"))

        raw_values: list[float] = []
        non_positive_count = 0
        if measurement_kind:
            for column in measurement_columns:
                parsed = parse_float(value_of(row, column))
                if parsed is None:
                    continue
                if parsed <= 0:
                    non_positive_count += 1
                    continue
                raw_values.append(parsed)
            hint = "px" if any("px" in norm_header(c) for c in measurement_columns) else ""
        else:
            single_col = columns.get("single_diameter_mm") or columns.get("single_diameter_px")
            parsed = parse_float(value_of(row, single_col))
            hint = "px" if single_col == columns.get("single_diameter_px") else ""
            if parsed is not None:
                if parsed <= 0:
                    non_positive_count += 1
                else:
                    raw_values.append(parsed)

        if not raw_values:
            skipped_rows += 1
            warnings.append(f"第 {index} 行（{sample_id}）没有有效测量值，已跳过")
            continue

        factor, basis = resolve_unit_factor(row, columns, args, hint, warnings)
        conversion_counts[basis] += 1

        if factor is None:
            skipped_rows += 1
            warnings.append(f"第 {index} 行（{sample_id}）无法换算为 mm，已跳过")
            continue

        values_mm = [value * factor for value in raw_values]
        if measurement_kind == "radius":
            values_mm = [value * 2.0 for value in values_mm]

        row_plug = parse_float(value_of(row, columns.get("plug")))
        inferred_plug = row_plug if row_plug is not None else tip_plug_value(tip_color, args)
        if args.plug_diameter_mm is not None:
            effective_plug = float(args.plug_diameter_mm)
            plug_source = "command_line"
            if inferred_plug is not None and abs(inferred_plug - effective_plug) > 1e-9:
                warnings.append(
                    f"实验 {experiment} 的行内菌饼直径 {inferred_plug:g} mm 与命令行 "
                    f"{effective_plug:g} mm 不一致；命令行优先"
                )
        elif inferred_plug is not None:
            effective_plug = float(inferred_plug)
            plug_source = "row_or_tip"
        else:
            effective_plug = 6.0
            plug_source = "default_6"

        colony_mean = statistics.fmean(values_mm)
        colony_sd = sample_sd(values_mm)
        colony_cv = (colony_sd / colony_mean) if colony_sd is not None and colony_mean > 0 else None
        reasons: list[str] = []
        if non_positive_count:
            reasons.append(f"忽略 {non_positive_count} 个非正测量值")
        if len(values_mm) < int(args.min_measurements):
            reasons.append(f"有效测量值仅 {len(values_mm)} 个")
        elif len(values_mm) < int(args.expected_measurements):
            reasons.append(f"测量值不足 {int(args.expected_measurements)} 个（{len(values_mm)} 个）")
        if colony_mean < effective_plug:
            reasons.append("菌落平均直径小于菌饼直径，疑似测量或标定错误")
        if colony_cv is not None:
            threshold = args.irregular_cv_threshold if "irregular" in norm_header(edge_type) else args.cv_threshold
            if colony_cv > threshold:
                reasons.append(f"CV={colony_cv:.3f} 超过阈值 {threshold:.2f}")
        auto_review = truthy(value_of(row, columns.get("needs_review")))
        auto_reasons = value_of(row, columns.get("review_reasons"))
        if auto_review:
            reasons.append(auto_reasons or "输入行已标记 needs_review")
        ecd_mm = parse_float(value_of(row, columns.get("ecd")))
        ecd_deviation = None
        if ecd_mm is not None and ecd_mm > 0:
            ecd_deviation = abs(colony_mean - ecd_mm) / ecd_mm
            if ecd_deviation > args.ecd_review_threshold:
                reasons.append(
                    f"十字直径与等效圆直径偏差 {ecd_deviation:.1%} 超过阈值 {args.ecd_review_threshold:.0%}"
                )

        is_ck = truthy(value_of(row, columns.get("is_ck"))) or norm_header(treatment) in ck_names
        records.append(
            {
                "experiment": experiment,
                "sample_id": sample_id,
                "treatment": treatment,
                "replicate": replicate,
                "image_path": image_path,
                "edge_type": edge_type,
                "tip_color": tip_color,
                "unit_basis": basis,
                "n_measurements": len(values_mm),
                "diameter_mm": colony_mean,
                "sd_mm": colony_sd,
                "cv": colony_cv,
                "ecd_mm": ecd_mm,
                "ecd_deviation": ecd_deviation,
                "min_mm": min(values_mm),
                "max_mm": max(values_mm),
                "plug_diameter_mm": effective_plug,
                "plug_source": plug_source,
                "is_ck": is_ck,
                "needs_review": bool(reasons),
                "review_reasons": "; ".join(reasons),
                "values_mm": values_mm,
                "notes": value_of(row, columns.get("notes")),
            }
        )

    if not records:
        print("错误：没有任何可计算的菌落记录", file=sys.stderr)
        return 2

    # Resolve one plug diameter per experiment.
    experiments = sorted({record["experiment"] for record in records})
    experiment_plug: dict[str, float] = {}
    experiment_plug_source: dict[str, str] = {}
    for experiment in experiments:
        subset = [record for record in records if record["experiment"] == experiment]
        if args.plug_diameter_mm is not None:
            experiment_plug[experiment] = float(args.plug_diameter_mm)
            experiment_plug_source[experiment] = "command_line"
        else:
            explicit = [record["plug_diameter_mm"] for record in subset if record["plug_source"] == "row_or_tip"]
            chosen = mode_or_none(explicit)
            if chosen is None:
                chosen = 6.0
                experiment_plug_source[experiment] = "default_6"
            else:
                experiment_plug_source[experiment] = "row_or_tip_mode"
            experiment_plug[experiment] = float(chosen)

        distinct = {round(record["plug_diameter_mm"], 6) for record in subset}
        if len(distinct) > 1:
            warnings.append(
                f"实验 {experiment} 存在多个菌饼直径 {sorted(distinct)}；实验级取 "
                f"{experiment_plug[experiment]:g} mm"
            )
        log(f"实验 {experiment}: 菌饼直径 {experiment_plug[experiment]:g} mm（{experiment_plug_source[experiment]}）")

    # Colony output rows.
    colony_rows: list[dict] = []
    for record in records:
        record["plug_diameter_mm"] = experiment_plug[record["experiment"]]
        row = {
            "experiment_id": record["experiment"],
            "sample_id": record["sample_id"],
            "treatment": record["treatment"],
            "replicate": record["replicate"],
            "is_ck": "true" if record["is_ck"] else "false",
            "image_path": record["image_path"],
            "edge_type": record["edge_type"],
            "tip_color": record["tip_color"],
            "unit_basis": record["unit_basis"],
            "n_measurements": record["n_measurements"],
            "diameter_mm": round_or_none(record["diameter_mm"]),
            "sd_mm": round_or_none(record["sd_mm"]),
            "cv": round_or_none(record["cv"]),
            "ecd_mm": round_or_none(record.get("ecd_mm")),
            "ecd_deviation": round_or_none(record.get("ecd_deviation")),
            "min_mm": round_or_none(record["min_mm"]),
            "max_mm": round_or_none(record["max_mm"]),
            "plug_diameter_mm": record["plug_diameter_mm"],
            "needs_review": "true" if record["needs_review"] else "false",
            "review_reasons": record["review_reasons"],
            "notes": record["notes"],
        }
        for idx, value in enumerate(record["values_mm"], start=1):
            row[f"m{idx}_mm"] = round(value, 4)
        colony_rows.append(row)

    treatment_groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in records:
        treatment_groups[(record["experiment"], record["treatment"])].append(record)

    summary_rows: list[dict] = []
    efficacy_rows: list[dict] = []
    experiments_json: OrderedDict = OrderedDict()

    for experiment in experiments:
        subset = [record for record in records if record["experiment"] == experiment]
        ck_records = [record for record in subset if record["is_ck"]]
        if not ck_records:
            warnings.append(f"实验 {experiment} 找不到 CK，无法计算防效")
            ck_mean = None
            ck_n = 0
            ck_needs_review = True
            ck_reason = "找不到 CK"
        else:
            ck_mean = statistics.fmean(record["diameter_mm"] for record in ck_records)
            ck_n = len(ck_records)
            ck_reason_parts = []
            if ck_n < int(args.expected_replicates):
                ck_reason_parts.append(f"CK 重复数不足（{ck_n} < {int(args.expected_replicates)}）")
            if any(record["needs_review"] for record in ck_records):
                ck_reason_parts.append("CK 中存在需要复核的菌落")
            ck_needs_review = bool(ck_reason_parts)
            ck_reason = "; ".join(ck_reason_parts)

        plug = experiment_plug[experiment]
        if args.formula_mode == "plug_corrected":
            ck_growth_mean = ck_mean - plug if ck_mean is not None else None
            ck_calculable = ck_mean is not None and ck_mean > plug
            invalid_reason = f"CK 平均直径 {ck_mean:.3f} mm 不大于菌饼直径 {plug:g} mm" if ck_mean is not None else "缺少 CK"
        else:
            ck_growth_mean = ck_mean
            ck_calculable = ck_mean is not None and ck_mean > 0
            invalid_reason = f"CK 平均直径 {ck_mean:.3f} mm 不大于 0" if ck_mean is not None else "缺少 CK"
        if ck_mean is not None and not ck_calculable:
            warnings.append(f"实验 {experiment} {invalid_reason}，无法计算防效")

        experiment_treatments = []
        for (group_experiment, treatment), group in sorted(treatment_groups.items()):
            if group_experiment != experiment:
                continue
            diameters = [record["diameter_mm"] for record in group]
            group_mean = statistics.fmean(diameters)
            group_sd = sample_sd(diameters)
            total_measurements = sum(record["n_measurements"] for record in group)
            replicates = {record["replicate"] for record in group if record["replicate"]}
            n_replicates = len(replicates) if replicates else len(group)
            is_ck = bool(group and group[0]["is_ck"])
            reason_parts = []
            if n_replicates < int(args.expected_replicates):
                reason_parts.append(f"重复数不足（{n_replicates} < {int(args.expected_replicates)}）")
            if total_measurements < int(args.expected_total_measurements):
                reason_parts.append(f"总测量值不足 {int(args.expected_total_measurements)} 个（{total_measurements} 个）")
            if any(record["needs_review"] for record in group):
                reason_parts.append("组内存在需要复核的菌落")
            if not is_ck and ck_mean is None:
                reason_parts.append("缺少 CK，无法计算防效")
            if not is_ck and ck_mean is not None and not ck_calculable:
                reason_parts.append(invalid_reason + "，无法计算防效")

            if is_ck:
                efficacy = 0.0
                efficacy_status = "ck_reference"
            elif ck_calculable:
                if args.formula_mode == "plug_corrected":
                    denominator = ck_mean - plug
                else:
                    denominator = ck_mean
                efficacy = (ck_mean - group_mean) / denominator * 100.0
                efficacy_status = "computed"
            else:
                efficacy = None
                efficacy_status = "unavailable"

            summary_rows.append(
                {
                    "experiment_id": experiment,
                    "treatment": treatment,
                    "is_ck": "true" if is_ck else "false",
                    "n_colonies": len(group),
                    "n_replicates": n_replicates,
                    "total_measurements": total_measurements,
                    "mean_diameter_mm": round(group_mean, 4),
                    "sd_mm": round_or_none(group_sd),
                    "min_mm": round(min(diameters), 4),
                    "max_mm": round(max(diameters), 4),
                    "needs_review": "true" if reason_parts else "false",
                    "review_reasons": "; ".join(reason_parts),
                }
            )
            efficacy_rows.append(
                {
                    "experiment_id": experiment,
                    "ck_mean_diameter_mm": round_or_none(ck_mean),
                    "ck_n": ck_n,
                    "plug_diameter_mm": plug,
                    "plug_source": experiment_plug_source[experiment],
                    "formula_mode": args.formula_mode,
                    "ck_growth_mean_mm": round_or_none(ck_growth_mean),
                    "denominator_mm": round_or_none((ck_mean - plug) if args.formula_mode == "plug_corrected" and ck_mean is not None else ck_mean),
                    "treatment": treatment,
                    "is_ck": "true" if is_ck else "false",
                    "treatment_total_measurements": total_measurements,
                    "treatment_mean_diameter_mm": round(group_mean, 4),
                    "treatment_growth_mm": round_or_none(group_mean - plug if args.formula_mode == "plug_corrected" else group_mean),
                    "treatment_sd_mm": round_or_none(group_sd),
                    "treatment_n_colonies": len(group),
                    "treatment_n_replicates": n_replicates,
                    "efficacy_percent": round_or_none(efficacy),
                    "efficacy_status": efficacy_status,
                    "needs_review": "true" if reason_parts else "false",
                    "review_reasons": "; ".join(reason_parts),
                }
            )
            experiment_treatments.append(
                {
                    "treatment": treatment,
                    "is_ck": is_ck,
                    "n_colonies": len(group),
                    "n_replicates": n_replicates,
                    "total_measurements": total_measurements,
                    "mean_diameter_mm": round(group_mean, 4),
                    "sd_mm": round_or_none(group_sd),
                    "efficacy_percent": round_or_none(efficacy),
                    "efficacy_status": efficacy_status,
                    "needs_review": bool(reason_parts),
                    "review_reasons": reason_parts,
                }
            )

        experiments_json[experiment] = OrderedDict(
            [
                ("ck_mean_diameter_mm", round_or_none(ck_mean)),
                ("ck_n", ck_n),
                ("ck_needs_review", ck_needs_review),
                ("ck_review_reasons", ck_reason),
                ("plug_diameter_mm", plug),
                ("plug_source", experiment_plug_source[experiment]),
                ("formula_mode", args.formula_mode),
                ("ck_growth_mean_mm", round_or_none(ck_growth_mean)),
                ("treatments", experiment_treatments),
            ]
        )

    # Write CSV outputs.
    colony_fields = [
        "experiment_id", "sample_id", "treatment", "replicate", "is_ck", "image_path",
        "edge_type", "tip_color", "unit_basis", "n_measurements", "diameter_mm",
        "sd_mm", "cv", "ecd_mm", "ecd_deviation", "min_mm", "max_mm", "plug_diameter_mm", "needs_review",
        "review_reasons", "notes",
    ]
    max_measurements = max((record["n_measurements"] for record in records), default=0)
    colony_fields += [f"m{i}_mm" for i in range(1, max_measurements + 1)]

    summary_fields = [
        "experiment_id", "treatment", "is_ck", "n_colonies", "n_replicates", "total_measurements",
        "mean_diameter_mm", "sd_mm", "min_mm", "max_mm", "needs_review", "review_reasons",
    ]
    efficacy_fields = [
        "experiment_id", "ck_mean_diameter_mm", "ck_growth_mean_mm", "ck_n", "plug_diameter_mm", "plug_source",
        "formula_mode", "denominator_mm", "treatment", "is_ck", "treatment_total_measurements",
        "treatment_mean_diameter_mm", "treatment_growth_mm", "treatment_sd_mm",
        "treatment_n_colonies", "treatment_n_replicates", "efficacy_percent",
        "efficacy_status", "needs_review", "review_reasons",
    ]

    def write_csv(path: Path, fields: list[str], data: list[dict]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(data)

    colony_path = output_dir / "colony_measurements.csv"
    summary_path = output_dir / "treatment_summary.csv"
    efficacy_csv_path = output_dir / "efficacy_results.csv"
    efficacy_json_path = output_dir / "efficacy_result.json"
    log_path = output_dir / "run_log.txt"

    write_csv(colony_path, colony_fields, colony_rows)
    write_csv(summary_path, summary_fields, summary_rows)
    write_csv(efficacy_csv_path, efficacy_fields, efficacy_rows)

    review_count = sum(1 for row in colony_rows if row["needs_review"] == "true")
    result_json = OrderedDict(
        [
            ("schema_version", "1.0"),
            ("generated_at", datetime.now(timezone.utc).isoformat()),
            ("input_csv", str(input_path)),
            ("parameters", {
                "dish_diameter_mm": args.dish_diameter_mm,
                "plug_diameter_mm_cli": args.plug_diameter_mm,
                "formula_mode": args.formula_mode,
                "yellow_plug_mm": args.yellow_plug_mm,
                "blue_plug_mm": args.blue_plug_mm,
                "ck_names": sorted(ck_names),
                "expected_measurements_per_sample": args.expected_measurements,
                "expected_total_measurements": args.expected_total_measurements,
                "min_measurements": args.min_measurements,
                "expected_replicates": args.expected_replicates,
                "cv_threshold": args.cv_threshold,
                "irregular_cv_threshold": args.irregular_cv_threshold,
                "ecd_review_threshold": args.ecd_review_threshold,
            }),
            ("summary", {
                "input_rows": len(rows),
                "valid_colonies": len(records),
                "skipped_rows": skipped_rows,
                "colonies_needing_review": review_count,
                "experiments": len(experiments),
                "conversion_basis_counts": dict(conversion_counts),
            }),
            ("experiments", experiments_json),
            ("warnings", warnings),
            ("outputs", {
                "colony_measurements_csv": str(colony_path),
                "treatment_summary_csv": str(summary_path),
                "efficacy_results_csv": str(efficacy_csv_path),
                "efficacy_result_json": str(efficacy_json_path),
                "run_log": str(log_path),
            }),
        ]
    )
    efficacy_json_path.write_text(json.dumps(result_json, ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"有效菌落 {len(records)} 个，跳过 {skipped_rows} 行，需复核 {review_count} 个")
    for warning in warnings:
        log(f"警告：{warning}")
    log(f"输出：{colony_path}")
    log(f"输出：{summary_path}")
    log(f"输出：{efficacy_csv_path}")
    log(f"输出：{efficacy_json_path}")
    log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    if args.fail_on_review and review_count:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
