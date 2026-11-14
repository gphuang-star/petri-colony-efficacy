# -*- coding: utf-8 -*-
"""Number-to-treatment mapping and reversible, previewed image naming.

All filesystem changes are limited to the selected image folder. OCR produces
suggestions; only an explicit user confirmation makes an image exportable.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import threading
import unicodedata
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
OUTPUT_NAME = "菌落测量输出"
SESSION_NAME = "编号对应.json"
MANIFEST_FIELDS = [
    "experiment_id", "sample_id", "treatment", "replicate", "image_path",
    "plug_diameter_mm", "dish_diameter_mm", "strain", "treatment_code",
    "original_path", "original_name", "mapping_status", "replicate_status",
]
_BAD_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


def _normal_text(value):
    return unicodedata.normalize("NFKC", str(value if value is not None else "")).strip()


def normalize_code(value):
    """Keep leading zeroes: treatment 01 need not mean treatment 1."""
    code = _normal_text(value)
    if not re.fullmatch(r"[0-9]{1,12}", code):
        raise ValueError("处理编号须为 1～12 位数字，例如 1、2、11、01")
    return code


def normalize_replicate(value):
    rep = _normal_text(value)
    if not rep:
        return ""
    if not re.fullmatch(r"[0-9]{1,6}", rep) or int(rep) < 1:
        raise ValueError("重复号须为正整数；没有写重复号时请留空")
    return str(int(rep))


def parse_label(text):
    """Return (code, replicate) for an entire numeric label, or ('', '')."""
    text = _normal_text(text).translate(str.maketrans({"–": "-", "—": "-", "−": "-"}))
    match = re.fullmatch(r"([0-9]{1,12})(?:\s*-\s*([0-9]{1,6}))?", text)
    if not match:
        return "", ""
    try:
        return normalize_code(match[1]), normalize_replicate(match[2] or "")
    except ValueError:
        return "", ""


def _filename_component(text, label, optional=False):
    value = _normal_text(text)
    if not value and optional:
        return ""
    if not value:
        raise ValueError(f"{label}不能为空")
    if len(value) > 80 or _BAD_FILENAME.search(value) or value.endswith((".", " ")):
        raise ValueError(f"{label}含不适合文件名的字符或超过 80 字：{value}")
    if value in {".", ".."} or _RESERVED.match(value):
        raise ValueError(f"{label}不能使用 Windows 保留名称：{value}")
    return value


def _treatment_text(value):
    # Concentrations legitimately contain units such as mg/L. Keep this text in
    # manifests, and independently make a safe filename component below.
    value = str(value if value is not None else "").strip()
    if not value or len(value) > 120 or re.search(r"[\x00-\x1f]", value):
        raise ValueError("处理全名不能为空、超过 120 字或含控制字符")
    return value


def _treatment_filename(value):
    name = _BAD_FILENAME.sub("_", value).rstrip(". ")
    if not name or name in {".", ".."}:
        name = "处理"
    if _RESERVED.match(name):
        name = "_" + name
    return name


def _measurement_params(dish_mm, plug_mm):
    values = []
    for value, label in ((dish_mm, "培养皿直径"), (plug_mm, "菌饼直径")):
        if value is None:
            values.append(None)
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{label}须为有限正数") from None
        if isinstance(value, bool) or not math.isfinite(number) or number <= 0:
            raise ValueError(f"{label}须为有限正数")
        values.append(number)
    dish, plug = values
    if dish is not None and plug is not None and plug >= dish:
        raise ValueError("菌饼直径须小于培养皿直径")
    return dish, plug


def _digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fingerprint(path):
    path = Path(path)
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": _digest(path)}


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8", newline="\n") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _thumb(path):
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((440, 340))
            buf = io.BytesIO()
            image.save(buf, "JPEG", quality=78)
            return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


def _path_key(path):
    return str(Path(path).absolute()).replace("\\", "/").casefold()


def _assert_no_collisions(rows):
    targets = [_path_key(row["target"]) for row in rows]
    if len(set(targets)) != len(targets):
        raise ValueError("计划中有重复目标文件名，请区分处理名称或重复号")
    source_keys = {_path_key(row["source"]) for row in rows}
    parents = {}
    for row in rows:
        target = Path(row["target"])
        if target.parent not in parents:
            parents[target.parent] = list(target.parent.iterdir()) if target.parent.exists() else []
        for existing in parents[target.parent]:
            if _path_key(existing) == _path_key(target) and _path_key(existing) not in source_keys:
                raise FileExistsError(f"目标文件已存在，不能覆盖：{existing}")


def _transactional_moves(rows):
    """Stage every source first, so name cycles and case-only renames are safe."""
    _assert_no_collisions(rows)
    staged = []
    committed = []
    try:
        for row in rows:
            source, target = Path(row["source"]), Path(row["target"])
            temp = source.with_name(".naming-" + uuid.uuid4().hex + source.suffix)
            if temp.exists():
                raise FileExistsError(str(temp))
            os.rename(source, temp)
            staged.append((source, target, temp))
        for source, target, temp in staged:
            if target.exists():
                raise FileExistsError(f"目标文件已出现：{target}")
            os.rename(temp, target)
            committed.append((source, target, temp))
    except Exception as exc:
        rollback_errors = []
        # Move committed destinations back into their own temporary slots first.
        for source, target, temp in reversed(committed):
            try:
                if temp.exists():
                    raise FileExistsError(str(temp))
                os.rename(target, temp)
            except Exception as err:
                rollback_errors.append(str(err))
        for source, target, temp in reversed(staged):
            if temp.exists():
                try:
                    if source.exists():
                        raise FileExistsError(str(source))
                    os.rename(temp, source)
                except Exception as err:
                    rollback_errors.append(str(err))
        if rollback_errors:
            raise RuntimeError(f"改名失败且部分回滚未完成：{exc}；" + "；".join(rollback_errors)) from exc
        raise


class NamingSession:
    def __init__(self, folder):
        self.folder = Path(folder).expanduser().resolve()
        if not self.folder.is_dir():
            raise ValueError("图片文件夹不存在")
        self.output_root = self.folder / OUTPUT_NAME
        self.session_path = self.output_root / SESSION_NAME
        self.mapping = []
        self.experiment_id = self.folder.name
        self.strain = ""
        self.manifest_path = ""
        self.last_transaction_id = ""
        self._transactions = {}
        self._preview = None
        self._lock = threading.RLock()
        self.images = []
        files = []
        for path in self.folder.rglob("*"):
            relative = path.relative_to(self.folder)
            if any(part == OUTPUT_NAME or part.startswith(".naming") for part in relative.parts):
                continue
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            # A selected directory may contain junctions/symlinks to other data.
            try:
                path.resolve().relative_to(self.folder)
            except ValueError:
                continue
            files.append(path)
        for path in sorted(files, key=lambda p: p.relative_to(self.folder).as_posix().casefold()):
            relative = path.relative_to(self.folder).as_posix()
            code, rep = parse_label(path.stem)
            self.images.append({
                "id": hashlib.sha256(relative.encode("utf-8")).hexdigest()[:20],
                "name": path.name, "relative_path": relative, "image_path": str(path),
                "original_path": str(path), "original_name": path.name,
                "fingerprint": _fingerprint(path), "thumb": _thumb(path),
                "code": code, "replicate": rep, "confirmed": False,
                "ocr_text": "", "ocr_score": None,
            })
        if not self.images:
            raise ValueError("该文件夹（含子文件夹）没有可用图片")
        self._restore()

    def _restore(self):
        if not self.session_path.exists():
            return
        try:
            saved = json.loads(self.session_path.read_text(encoding="utf-8"))
            # Never trust paths recorded by a different batch.
            if _path_key(saved.get("folder", "")) != _path_key(self.folder):
                return
            self.mapping = self._validated_mapping(saved.get("mapping", []))
            self.experiment_id = str(saved.get("experiment_id", self.folder.name))
            self.strain = _filename_component(saved.get("strain", ""), "菌株名称", optional=True)
            by_path = {i["relative_path"]: i for i in saved.get("images", [])}
            for image in self.images:
                old = by_path.get(image["relative_path"])
                if old and old.get("fingerprint") == image["fingerprint"]:
                    for key in ("id", "code", "replicate", "confirmed", "ocr_text", "ocr_score", "original_name", "original_path"):
                        if key in old:
                            image[key] = old[key]
                    if image["code"] not in self._mapping_dict():
                        image["confirmed"] = False
            self._transactions = saved.get("transactions", {})
            self.last_transaction_id = saved.get("last_transaction_id", "")
            manifest = saved.get("manifest_path", "")
            if manifest and Path(manifest).is_file():
                try:
                    Path(manifest).resolve().relative_to(self.output_root.resolve())
                    self.manifest_path = manifest
                except ValueError:
                    pass
        except (ValueError, KeyError, TypeError, OSError):
            # Broken metadata must not attach an old treatment to a new photo.
            self.mapping = []
            self.manifest_path = ""

    @staticmethod
    def _validated_mapping(mapping):
        if isinstance(mapping, dict):
            mapping = [{"code": key, "treatment": value} for key, value in mapping.items()]
        if not isinstance(mapping, list):
            raise ValueError("对应表格式应为编号与处理名称列表")
        result, seen = [], set()
        for row in mapping:
            code = normalize_code(row.get("code", ""))
            treatment = _treatment_text(row.get("treatment", ""))
            if code in seen:
                raise ValueError(f"处理编号重复：{code}")
            seen.add(code)
            result.append({"code": code, "treatment": treatment})
        return result

    def _mapping_dict(self):
        return {row["code"]: row["treatment"] for row in self.mapping}

    def _save(self):
        saved_images = [{key: value for key, value in image.items() if key != "thumb"} for image in self.images]
        _atomic_json(self.session_path, {
            "schema_version": 1, "folder": str(self.folder), "mapping": self.mapping,
            "experiment_id": self.experiment_id, "strain": self.strain,
            "images": saved_images, "manifest_path": self.manifest_path,
            "transactions": self._transactions, "last_transaction_id": self.last_transaction_id,
        })

    def state(self):
        with self._lock:
            mapping = self._mapping_dict()
            return {
                "ok": True, "folder": str(self.folder), "mapping": [dict(row) for row in self.mapping],
                "experiment_id": self.experiment_id, "strain": self.strain,
                "manifest_path": self.manifest_path, "last_transaction_id": self.last_transaction_id,
                "images": [dict(image, treatment=mapping.get(image["code"], "")) for image in self.images],
                "confirmed_count": sum(bool(i["confirmed"]) for i in self.images),
            }

    def configure(self, mapping, experiment_id="", strain=""):
        with self._lock:
            new_mapping = self._validated_mapping(mapping)
            new_experiment = _normal_text(experiment_id) or self.folder.name
            if len(new_experiment) > 120 or re.search(r"[\x00-\x1f]", new_experiment):
                raise ValueError("实验名称过长或含控制字符")
            new_strain = _filename_component(strain, "菌株名称", optional=True)
            changed = self._mapping_dict() != {row["code"]: row["treatment"] for row in new_mapping}
            changed = changed or self.experiment_id != new_experiment or self.strain != new_strain
            self.mapping, self.experiment_id, self.strain = new_mapping, new_experiment, new_strain
            if changed:
                for image in self.images:
                    image["confirmed"] = False
                self.manifest_path = ""
                self._preview = None
            self._save()
            return self.state()

    def _find(self, image_id):
        for image in self.images:
            if image["id"] == image_id:
                return image
        raise ValueError("找不到该图片，请重新载入文件夹")

    def assign(self, image_id, code, replicate="", confirmed=True):
        with self._lock:
            code = _normal_text(code)
            if not code and not confirmed:
                # The blank option lets a user discard an incorrect suggestion.
                # It never confirms an unidentified image.
                code = ""
            else:
                code = normalize_code(code)
            if code and code not in self._mapping_dict():
                raise ValueError(f"编号 {code} 尚未填写对应的处理全名")
            image = self._find(image_id)
            image.update(code=code, replicate=normalize_replicate(replicate), confirmed=bool(confirmed))
            self._preview = None
            self.manifest_path = ""
            self._save()
            return self.state()

    def suggest(self, image_id, code="", replicate="", ocr_text="", ocr_score=None):
        with self._lock:
            image = self._find(image_id)
            if image["confirmed"]:
                return self.state()
            code = _normal_text(code)
            if code not in self._mapping_dict():
                code = ""
            try:
                replicate = normalize_replicate(replicate)
            except ValueError:
                # OCR is best-effort: an invalid repeat such as 0 must remain
                # unknown, rather than interrupt recognition of the next image.
                replicate = ""
            image.update(code=code, replicate=replicate, confirmed=False,
                         ocr_text=str(ocr_text), ocr_score=ocr_score)
            self._preview = None
            self.manifest_path = ""
            self._save()
            return self.state()

    def _check_confirmed(self):
        mapping = self._mapping_dict()
        if not mapping:
            raise ValueError("请先填写编号与处理名称的对应表")
        pending = [i["name"] for i in self.images if not i["confirmed"] or i["code"] not in mapping]
        if pending:
            raise ValueError(f"还有 {len(pending)} 张图片未确认处理编号，例如：{pending[0]}")
        seen = set()
        for image in self.images:
            rep = normalize_replicate(image["replicate"])
            pair = (image["code"], rep)
            if rep and pair in seen:
                raise ValueError(f"编号 {image['code']} 的重复号 {rep} 出现多次；复拍请先挑选一张，不能当作独立重复")
            if rep:
                seen.add(pair)
            path = Path(image["image_path"])
            try:
                path.resolve(strict=True).relative_to(self.folder)
            except (ValueError, OSError):
                raise ValueError(f"图片已移动或超出所选文件夹：{image['name']}")
            if _fingerprint(path) != image["fingerprint"]:
                raise ValueError(f"图片已发生变化，请重新载入并确认：{image['name']}")

    def _snapshot(self):
        value = {"mapping": self.mapping, "experiment_id": self.experiment_id, "strain": self.strain,
                 "images": [{key: image[key] for key in ("id", "image_path", "code", "replicate", "confirmed", "fingerprint")} for image in self.images]}
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def _new_output(self, prefix):
        return self.output_root / (prefix + "_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8])

    def _rows(self, mode, directory):
        mapping = self._mapping_dict()
        rows = []
        for image in self.images:
            treatment = mapping[image["code"]]
            parts = ([self.strain] if self.strain else []) + [_treatment_filename(treatment)]
            parts.append("r" + image["replicate"] if image["replicate"] else "img" + image["id"][:10])
            filename = "_".join(parts) + Path(image["image_path"]).suffix.lower()
            if len(filename) > 220:
                raise ValueError("生成的文件名过长，请缩短菌株名或处理名")
            source = Path(image["image_path"])
            target = source.parent / filename if mode == "rename" else directory / filename
            rows.append({
                "id": image["id"], "source": str(source), "target": str(target),
                "code": image["code"], "treatment": treatment, "replicate": image["replicate"],
                "original_path": image["original_path"], "original_name": image["original_name"],
                "fingerprint": dict(image["fingerprint"]),
            })
        return rows

    def preview(self, mode="copy"):
        with self._lock:
            if mode not in {"copy", "rename"}:
                raise ValueError("请选择生成改名副本或修改原文件名")
            self._check_confirmed()
            directory = self._new_output("编号改名")
            rows = self._rows(mode, directory)
            _assert_no_collisions(rows)
            warnings = []
            unknown = sum(not image["replicate"] for image in self.images)
            if unknown:
                warnings.append(f"{unknown} 张未填写重复号，文件名使用图片唯一标识；重复号保持未知，不按照片顺序编造")
            duplicates = [name for name, n in Counter(self._mapping_dict().values()).items() if n > 1]
            if duplicates:
                warnings.append("多个编号使用相同处理全名，测量时会汇总为同一处理：" + "、".join(duplicates))
            if any(_treatment_filename(name) != name for name in self._mapping_dict().values()):
                warnings.append("处理全名中的 / 等文件名不支持的字符已在目标文件名中替换为 _；清单保留处理全名原文")
            token = uuid.uuid4().hex
            self._preview = {"token": token, "mode": mode, "output_dir": str(directory),
                             "rows": rows, "warnings": warnings, "snapshot": self._snapshot()}
            return {"ok": True, **self._preview}

    def _manifest_rows(self, rows, target_paths=False, dish_mm=None, plug_mm=None):
        result = []
        for row in rows:
            result.append({
                "experiment_id": self.experiment_id, "sample_id": row["id"],
                "treatment": row["treatment"], "replicate": row["replicate"],
                "image_path": row["target"] if target_paths else row["source"],
                "plug_diameter_mm": "" if plug_mm is None else plug_mm,
                "dish_diameter_mm": "" if dish_mm is None else dish_mm,
                "strain": self.strain, "treatment_code": row["code"],
                "original_path": row["original_path"], "original_name": row["original_name"],
                "mapping_status": "user_confirmed",
                "replicate_status": "user_label" if row["replicate"] else "unknown",
            })
        return result

    def _write_tables(self, directory, rows, target_paths=False, dish_mm=None, plug_mm=None):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "plate_manifest.csv"
        manifest_rows = self._manifest_rows(rows, target_paths, dish_mm, plug_mm)
        with path.open("x", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
            writer.writeheader()
            writer.writerows(manifest_rows)
        fields = ["id", "source", "target", "code", "treatment", "replicate", "original_path", "original_name"]
        with (directory / "mapping.csv").open("x", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        _atomic_json(directory / "treatment_codes.json", {
            "experiment_id": self.experiment_id, "strain": self.strain, "mapping": self.mapping,
        })
        return str(path)

    def write_manifest(self, directory=None, dish_mm=None, plug_mm=None):
        """Save confirmed metadata without changing any original image names."""
        with self._lock:
            dish_mm, plug_mm = _measurement_params(dish_mm, plug_mm)
            self._check_confirmed()
            directory = Path(directory).resolve() if directory else self._new_output("编号清单")
            try:
                directory.resolve().relative_to(self.output_root.resolve())
            except ValueError:
                raise ValueError("清单目录须在所选文件夹的菌落测量输出中")
            if directory.exists():
                raise FileExistsError(f"清单目录已存在：{directory}")
            rows = self._rows("copy", directory)
            for row in rows:
                row["target"] = row["source"]
            self.manifest_path = self._write_tables(directory, rows, False, dish_mm, plug_mm)
            self._save()
            return self.manifest_path

    def apply(self, token, mode="copy", dish_mm=None, plug_mm=None):
        with self._lock:
            dish_mm, plug_mm = _measurement_params(dish_mm, plug_mm)
            preview = self._preview
            if not preview or preview["token"] != token or preview["mode"] != mode:
                raise ValueError("改名预览已失效，请重新预览")
            self._check_confirmed()
            if preview["snapshot"] != self._snapshot():
                raise ValueError("对应表或图片分组已经改变，请重新预览")
            rows = preview["rows"]
            _assert_no_collisions(rows)
            directory = Path(preview["output_dir"])
            directory.mkdir(parents=True, exist_ok=False)
            transaction_id = uuid.uuid4().hex
            record_path = directory / "transaction.json"
            record = {"schema_version": 1, "transaction_id": transaction_id, "mode": mode,
                      "status": "applying", "folder": str(self.folder), "rows": rows,
                      "experiment_id": self.experiment_id, "strain": self.strain, "mapping": self.mapping,
                      "dish_diameter_mm": dish_mm, "plug_diameter_mm": plug_mm}
            _atomic_json(record_path, record)
            copied = []
            renamed = False
            try:
                if mode == "rename":
                    _transactional_moves(rows)
                    renamed = True
                else:
                    for row in rows:
                        target = Path(row["target"])
                        temp = directory / (".naming-" + uuid.uuid4().hex + target.suffix)
                        try:
                            shutil.copy2(row["source"], temp)
                            if _digest(temp) != row["fingerprint"]["sha256"]:
                                raise ValueError(f"复制期间图片发生变化：{row['source']}")
                            if target.exists():
                                raise FileExistsError(str(target))
                            os.rename(temp, target)
                            copied.append(target)
                        finally:
                            if temp.exists():
                                temp.unlink()
                manifest_path = self._write_tables(directory, rows, True, dish_mm, plug_mm)
                record.update(status="complete", manifest_path=manifest_path)
                _atomic_json(record_path, record)
            except Exception as exc:
                rollback_error = ""
                if renamed:
                    try:
                        _transactional_moves([dict(row, source=row["target"], target=row["source"]) for row in rows])
                    except Exception as err:
                        rollback_error = str(err)
                for path in copied:
                    try:
                        path.unlink()
                    except OSError as err:
                        rollback_error += "; " + str(err)
                record.update(status="failed", error=str(exc), rollback_error=rollback_error)
                _atomic_json(record_path, record)
                self._preview = None
                if rollback_error:
                    raise RuntimeError(f"操作失败，部分回滚未完成；详见 {record_path}：{rollback_error}") from exc
                raise
            if mode == "rename":
                for row in rows:
                    image = self._find(row["id"])
                    image.update(image_path=row["target"], name=Path(row["target"]).name,
                                 relative_path=Path(row["target"]).relative_to(self.folder).as_posix(),
                                 fingerprint=_fingerprint(row["target"]))
            self.manifest_path = manifest_path
            self.last_transaction_id = transaction_id
            self._transactions[transaction_id] = str(record_path)
            self._preview = None
            self._save()
            return {"ok": True, "output_dir": str(directory), "manifest_path": manifest_path,
                    "transaction_id": transaction_id, "count": len(rows), "mode": mode,
                    "rows": rows, "can_undo": mode == "rename"}

    def undo(self, transaction_id):
        with self._lock:
            path = self._transactions.get(transaction_id)
            if not path:
                raise ValueError("找不到本批次的改名记录")
            path = Path(path).resolve()
            try:
                path.relative_to(self.output_root.resolve())
            except ValueError:
                raise ValueError("改名记录不在本批次输出目录内")
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("transaction_id") != transaction_id or _path_key(record.get("folder", "")) != _path_key(self.folder):
                raise ValueError("改名记录与本批次不符")
            if record.get("mode") != "rename":
                raise ValueError("该记录为生成副本，原文件名没有改变，无需撤销")
            if record.get("status") != "complete":
                raise ValueError("该改名记录已撤销或尚未成功完成")
            rows = record["rows"]
            for row in rows:
                for key in ("source", "target"):
                    try:
                        Path(row[key]).resolve().relative_to(self.folder)
                    except ValueError:
                        raise ValueError("改名记录含所选文件夹外的路径")
                if not Path(row["target"]).is_file() or _fingerprint(row["target"]) != row["fingerprint"]:
                    raise ValueError(f"改名后的图片已移动或更改，不能自动撤销：{row['target']}")
            reverse = [dict(row, source=row["target"], target=row["source"]) for row in rows]
            _transactional_moves(reverse)
            record["status"] = "undone"
            _atomic_json(path, record)
            for row in rows:
                image = self._find(row["id"])
                image.update(image_path=row["source"], name=Path(row["source"]).name,
                             relative_path=Path(row["source"]).relative_to(self.folder).as_posix(),
                             fingerprint=_fingerprint(row["source"]))
            # Old manifests describe the renamed paths; retain them for the audit
            # and write a new usable manifest at the restored paths.
            self._preview = None
            self.manifest_path = ""
            warnings = []
            try:
                self._check_confirmed()
            except ValueError:
                # A user may edit a mapping between rename and undo. Restoring
                # filenames remains valid, but that edit still needs confirmation.
                warnings.append("原文件名已恢复；当前对应表有未确认项，请核对后重新生成清单")
            else:
                self.manifest_path = self.write_manifest(
                    dish_mm=record.get("dish_diameter_mm"),
                    plug_mm=record.get("plug_diameter_mm"))
            self._save()
            return {"ok": True, "count": len(rows), "transaction_id": transaction_id,
                    "manifest_path": self.manifest_path, "status": "undone", "warnings": warnings}
