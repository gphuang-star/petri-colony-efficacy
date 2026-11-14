"""CLI-level checks against temporary photos; no real experiment is changed."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "number_label_workflow.py"


def write_csv(path, fieldnames, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


class NumberLabelCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="petri-cli-")
        self.base = Path(self.tmp.name)
        self.photos = self.base / "照片"
        self.photos.mkdir()
        self.assignments = self.base / "assignments.csv"
        self.mapping = self.base / "mapping.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def photo(self, name, rgb=(20, 30, 40)):
        from PIL import Image
        path = self.photos / name
        Image.new("RGB", (32, 32), rgb).save(path)
        return path

    def run_cli(self, *args, ok=True):
        p = subprocess.run([sys.executable, "-X", "utf8", str(CLI), *map(str, args)],
                           text=True, encoding="utf-8", capture_output=True)
        if ok:
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
            return json.loads(p.stdout)
        self.assertNotEqual(p.returncode, 0, p.stderr + p.stdout)
        return json.loads(p.stderr)

    def scan(self):
        self.run_cli("scan", "--folder", self.photos, "--out", self.assignments)
        return read_csv(self.assignments)

    def apply_args(self):
        return ("apply", "--folder", self.photos, "--mapping", self.mapping,
                "--assignments", self.assignments, "--experiment-id", "实验甲",
                "--strain", "层出镰刀菌")

    def test_unconfirmed_and_missing_assignments_are_rejected(self):
        original = self.photo("IMG_100.jpg")
        self.photo("IMG_101.jpg")
        before = original.read_bytes()
        rows = self.scan()
        self.assertTrue(all(row["confirmed"] == "false" for row in rows))
        write_csv(self.mapping, ["code", "treatment"], [{"code": "1", "treatment": "吡噻菌胺"}])
        result = self.run_cli(*self.apply_args(), ok=False)
        self.assertIn("未人工确认", result["error"])
        rows = rows[:1]
        rows[0].update(code="1", confirmed="true")
        write_csv(self.assignments, list(rows[0]), rows)
        result = self.run_cli(*self.apply_args(), ok=False)
        self.assertIn("不一致", result["error"])
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(len(list(self.photos.rglob("*.jpg"))), 2)

    def test_copy_preserves_originals_and_explicit_codes_and_unknown_repeats(self):
        sources = [self.photo("IMG_100.jpg"), self.photo("IMG_101.jpg", (50, 60, 70)),
                   self.photo("IMG_102.jpg", (80, 90, 100))]
        original_bytes = {p: p.read_bytes() for p in sources}
        rows = self.scan()
        treatment_map = {"1": "吡噻菌胺", "11": "丙环唑 100 mg/L", "01": "CK"}
        write_csv(self.mapping, ["code", "treatment"],
                  [{"code": code, "treatment": treatment} for code, treatment in treatment_map.items()])
        for row, code in zip(rows, treatment_map):
            row.update(code=code, replicate="" if code == "1" else "2", confirmed="true")
        write_csv(self.assignments, list(rows[0]), rows)
        preview = self.run_cli(*self.apply_args(), "--preview")
        self.assertEqual(len(preview["rows"]), 3)
        self.assertEqual(len(list(self.photos.rglob("*.jpg"))), 3)
        result = self.run_cli(*self.apply_args())
        self.assertEqual(result["count"], 3)
        manifest = read_csv(result["manifest_path"])
        self.assertEqual({row["treatment"] for row in manifest}, set(treatment_map.values()))
        self.assertEqual(next(row["replicate"] for row in manifest if row["treatment"] == "吡噻菌胺"), "")
        for source, before in original_bytes.items():
            self.assertEqual(source.read_bytes(), before)
        copied_hashes = {hashlib.sha256(Path(row["image_path"]).read_bytes()).hexdigest() for row in manifest}
        self.assertEqual(copied_hashes, {hashlib.sha256(value).hexdigest() for value in original_bytes.values()})

    def test_direct_rename_can_be_undone_by_transaction(self):
        source = self.photo("IMG_200.jpg")
        before = source.read_bytes()
        rows = self.scan()
        rows[0].update(code="11", replicate="3", confirmed="true")
        write_csv(self.mapping, ["code", "treatment"], [{"code": "11", "treatment": "丙环唑"}])
        write_csv(self.assignments, list(rows[0]), rows)
        result = self.run_cli(*self.apply_args(), "--mode", "rename")
        self.assertFalse(source.exists())
        self.assertEqual(len(list(self.photos.glob("*.jpg"))), 1)
        self.run_cli("undo", "--folder", self.photos, "--transaction-id", result["transaction_id"])
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(len(list(self.photos.glob("*.jpg"))), 1)

    def test_suggest_preserves_confirmed_and_exports_unconfirmed_and_failures(self):
        for index in range(4):
            self.photo(f"IMG_{index}.jpg", (index * 25, 20, 30))
        spec = importlib.util.spec_from_file_location("number_label_cli_for_test", CLI)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from label_workflow import NamingSession
        session = NamingSession(self.photos)
        mapping = [{"code": "1", "treatment": "吡噻菌胺"},
                   {"code": "11", "treatment": "丙环唑"}, {"code": "01", "treatment": "CK"}]
        session.configure(mapping, experiment_id="保留实验", strain="保留菌株")
        images = session.state()["images"]
        session.assign(images[0]["id"], "1", "1", confirmed=True)
        session.assign(images[3]["id"], "01", "2", confirmed=False)
        write_csv(self.mapping, ["code", "treatment"], mapping)
        called = []

        def recognize(path, codes):
            name = Path(path).name
            called.append(name)
            self.assertEqual(codes, ["1", "11", "01"])
            if name == "IMG_1.jpg":
                return dict(code="11", replicate="2", ocr_text="11-2", ocr_score=0.93,
                            reason="识别建议，尚未人工确认")
            if name == "IMG_2.jpg":
                return dict(code="", replicate="", ocr_text="?", ocr_score=None,
                            reason="多个候选，无法判定")
            raise RuntimeError("fixture OCR failed")

        fake_ocr = SimpleNamespace(availability=lambda: (True, "ready"), recognize=recognize)
        args = SimpleNamespace(mapping=self.mapping, out=self.assignments,
                               experiment_id=None, strain=None)
        with mock.patch.dict(sys.modules, {"label_ocr": fake_ocr}):
            result = module.suggest(args, session)
        self.assertFalse(result["ok"])
        self.assertEqual((result["recognized"], result["unread"], result["failed"],
                          result["preserved_confirmed"]), (1, 1, 1, 1))
        self.assertNotIn("IMG_0.jpg", called)
        rows = read_csv(self.assignments)
        by_name = {row["relative_path"]: row for row in rows}
        self.assertEqual((by_name["IMG_0.jpg"]["code"], by_name["IMG_0.jpg"]["confirmed"]), ("1", "true"))
        self.assertEqual((by_name["IMG_1.jpg"]["code"], by_name["IMG_1.jpg"]["replicate"],
                          by_name["IMG_1.jpg"]["confirmed"]), ("11", "2", "false"))
        self.assertEqual(by_name["IMG_2.jpg"]["code"], "")
        self.assertEqual(by_name["IMG_3.jpg"]["code"], "")
        self.assertIn("fixture OCR failed", by_name["IMG_3.jpg"]["ocr_reason"])
        self.assertEqual((session.state()["experiment_id"], session.state()["strain"]), ("保留实验", "保留菌株"))
        self.assertEqual(len(list(self.photos.glob("*.jpg"))), 4)

    def test_missing_ocr_is_clear_and_leaves_confirmed_mapping_intact(self):
        self.photo("IMG_0.jpg")
        spec = importlib.util.spec_from_file_location("number_label_cli_for_test", CLI)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from label_workflow import NamingSession
        session = NamingSession(self.photos)
        mapping = [{"code": "1", "treatment": "吡噻菌胺"}]
        session.configure(mapping, experiment_id="本批", strain="菌株")
        session.assign(session.state()["images"][0]["id"], "1", "1", confirmed=True)
        write_csv(self.mapping, ["code", "treatment"], mapping)
        fake_ocr = SimpleNamespace(availability=lambda: (False, "未安装 OCR 组件"))
        args = SimpleNamespace(mapping=self.mapping, out=self.assignments,
                               experiment_id="不能覆盖", strain="不能覆盖")
        with mock.patch.dict(sys.modules, {"label_ocr": fake_ocr}):
            with self.assertRaisesRegex(RuntimeError, "scan"):
                module.suggest(args, session)
        self.assertEqual((session.state()["experiment_id"], session.state()["strain"]), ("本批", "菌株"))
        self.assertTrue(session.state()["images"][0]["confirmed"])


if __name__ == "__main__":
    unittest.main()
