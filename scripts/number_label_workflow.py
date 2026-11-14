#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prepare, review and apply numeric plate-label mappings without guessing repeats.

Both the CLI and graphical app produce unconfirmed offline OCR suggestions.
Only explicitly reviewed CSV assignments can be copied or renamed.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "app"))

ASSIGNMENT_COLUMNS = [
    "id", "relative_path", "code", "replicate", "confirmed", "ocr_text", "ocr_score", "ocr_reason"
]


def _read_csv(path: str, required: set[str]) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = set(reader.fieldnames or [])
        missing = required - fields
        if missing:
            raise ValueError(f"{path} 缺少列：{', '.join(sorted(missing))}")
        rows = [{k: (v or "").strip() for k, v in row.items() if k is not None}
                for row in reader]
    if not rows:
        raise ValueError(f"{path} 没有数据行")
    return rows


def _confirmed(value: str) -> bool:
    value = str(value).strip().lower()
    if value in {"true", "1", "yes", "是", "已确认"}:
        return True
    if value in {"false", "0", "no", "否", "未确认", ""}:
        return False
    raise ValueError(f"confirmed 必须为 true 或 false，收到：{value!r}")


def _write_assignments(session, out, reasons=None):
    state = session.state()
    out_path = Path(out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=ASSIGNMENT_COLUMNS)
        writer.writeheader()
        for image in state["images"]:
            row = {key: image.get(key, "") for key in ASSIGNMENT_COLUMNS}
            row["confirmed"] = "true" if image.get("confirmed") else "false"
            row["ocr_reason"] = (reasons or {}).get(image["id"], "")
            writer.writerow(row)
    return out_path


def scan(args, session):
    state = session.state()
    out_path = _write_assignments(session, args.out)
    return {"ok": True, "count": len(state["images"]), "assignments": str(out_path),
            "message": "请核对每张图片的 code、replicate，再将 confirmed 设为 true。"}


def suggest(args, session):
    import label_ocr
    available, message = label_ocr.availability()
    if not available:
        raise RuntimeError(message + "；可使用 scan 导出表格后手动分配编号。")
    mapping = _read_csv(args.mapping, {"code", "treatment"})
    mapping = [{"code": row["code"], "treatment": row["treatment"]} for row in mapping]
    before = session.state()
    experiment_id = before.get("experiment_id", "") if args.experiment_id is None else args.experiment_id
    strain = before.get("strain", "") if args.strain is None else args.strain
    session.configure(mapping, experiment_id=experiment_id, strain=strain)
    state = session.state()
    codes = [row["code"] for row in state["mapping"]]
    reasons, errors = {}, []
    recognized = preserved = unread = 0
    for image in state["images"]:
        if image.get("confirmed"):
            preserved += 1
            reasons[image["id"]] = "已人工确认，保留原分配"
            continue
        try:
            result = label_ocr.recognize(image["image_path"], codes)
            session.suggest(image["id"], **{key: result.get(key, "" if key != "ocr_score" else None)
                                         for key in ("code", "replicate", "ocr_text", "ocr_score")})
            reasons[image["id"]] = result.get("reason", "识别建议，尚未人工确认")
            if result.get("code"):
                recognized += 1
            else:
                unread += 1
        except Exception as exc:
            # Do not keep a stale unconfirmed suggestion after a failed attempt.
            session.suggest(image["id"], code="", replicate="", ocr_text="", ocr_score=None)
            detail = f"{image['relative_path']}：{exc}"
            errors.append(detail)
            reasons[image["id"]] = "识别失败，请手动分配；" + str(exc)
    out_path = _write_assignments(session, args.out, reasons)
    result = {"ok": not errors, "count": len(state["images"]), "recognized": recognized,
              "unread": unread, "preserved_confirmed": preserved, "failed": len(errors),
              "assignments": str(out_path), "errors": errors,
              "message": "OCR 结果仍未确认；请对照原图核对 code、replicate，再将 confirmed 设为 true。"}
    if errors:
        result["error"] = f"{len(errors)} 张识别失败；分配表已保存，可手动填写后继续。"
    return result


def apply(args, session):
    mapping = _read_csv(args.mapping, {"code", "treatment"})
    mapping = [{"code": row["code"], "treatment": row["treatment"]} for row in mapping]
    assignments = _read_csv(args.assignments, {"id", "code", "replicate", "confirmed"})
    images = session.state()["images"]
    expected = {image["id"] for image in images}
    supplied = [row["id"] for row in assignments]
    if len(set(supplied)) != len(supplied):
        raise ValueError("分配表包含重复 id，请重新 scan 后逐图填写")
    missing, unknown = expected - set(supplied), set(supplied) - expected
    if missing or unknown:
        raise ValueError(f"分配表与当前图片不一致：缺少 {len(missing)} 张，多出 {len(unknown)} 张；请重新 scan")
    # Validate the whole review before changing the persisted batch configuration.
    unconfirmed = [row["id"] for row in assignments if not _confirmed(row["confirmed"])]
    if unconfirmed:
        raise ValueError(f"还有 {len(unconfirmed)} 张图片未人工确认，不能一键改名")
    session.configure(mapping, experiment_id=args.experiment_id, strain=args.strain)
    for row in assignments:
        session.assign(row["id"], row["code"], row["replicate"], confirmed=True)
    preview = session.preview(mode=args.mode)
    if not preview.get("ok", True):
        return preview
    if args.preview:
        return preview
    return session.apply(preview["token"], mode=args.mode)


def build_parser():
    parser = argparse.ArgumentParser(description="皿盖数字编号对应处理、一键改名与撤销")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("scan", help="扫描图片并导出待人工确认的逐图分配表")
    p.add_argument("--folder", required=True, help="原始照片文件夹，递归扫描并排除程序输出")
    p.add_argument("--out", required=True, help="逐图分配 CSV 保存路径")
    p = sub.add_parser("suggest", help="离线识别皿盖数字，导出未确认候选，不覆盖已人工确认分配")
    p.add_argument("--folder", required=True)
    p.add_argument("--mapping", required=True, help="含 code,treatment 列的本批编号对应表")
    p.add_argument("--out", required=True, help="OCR 逐图候选 CSV 保存路径")
    p.add_argument("--experiment-id", default=None, help="本批实验名称；省略时保留已保存的名称")
    p.add_argument("--strain", default=None, help="菌株/类别；省略时保留已保存的名称")
    p = sub.add_parser("apply", help="读取已确认分配表，预览或生成规范命名图片")
    p.add_argument("--folder", required=True)
    p.add_argument("--mapping", required=True, help="含 code,treatment 列的编号对应表")
    p.add_argument("--assignments", required=True, help="scan 导出的表，逐图填写并确认后使用")
    p.add_argument("--mode", choices=["copy", "rename"], default="copy",
                   help="copy=生成改名副本（默认）；rename=直接改原文件名，可撤销")
    p.add_argument("--experiment-id", default="", help="本批实验名称；勿混用不同实验的 CK")
    p.add_argument("--strain", default="", help="菌株/类别，作为显式元数据保存")
    p.add_argument("--preview", action="store_true", help="只输出改名预览，不复制或改名图片")
    p = sub.add_parser("undo", help="按事务记录撤销直接改名；不会删除生成副本")
    p.add_argument("--folder", required=True)
    p.add_argument("--transaction-id", required=True, help="apply 结果中的 transaction_id")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        from label_workflow import NamingSession
        session = NamingSession(args.folder)
        if args.command == "scan":
            result = scan(args, session)
        elif args.command == "suggest":
            result = suggest(args, session)
        elif args.command == "apply":
            result = apply(args, session)
        else:
            result = session.undo(args.transaction_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1
    except (ValueError, OSError, KeyError, RuntimeError, ImportError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
