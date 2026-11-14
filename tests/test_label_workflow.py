"""Behavioral checks for naming without touching real experiment images."""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "app" / "label_workflow.py"
spec = importlib.util.spec_from_file_location("label_workflow_tested", MODULE)
lw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lw)


class NamingWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def image(self, name, content=b"original image bytes"):
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def ready(self, assignments, mapping=None, strain="层出镰刀菌"):
        session = lw.NamingSession(self.folder)
        session.configure(mapping or {"1": "吡噻菌胺", "2": "CK"}, "试验甲", strain)
        for name, code, rep in assignments:
            image = next(i for i in session.images if i["relative_path"] == name)
            session.assign(image["id"], code, rep)
        return session

    def test_numeric_labels_keep_11_and_leading_zero_separate(self):
        self.assertEqual(lw.parse_label("１１－２"), ("11", "2"))
        self.assertEqual(lw.parse_label("01-03"), ("01", "3"))
        self.assertEqual(lw.parse_label("1"), ("1", ""))
        for text in ("camera123", "1.2", "-1", "1-0", "1 2"):
            self.assertEqual(lw.parse_label(text), ("", ""))
        self.image("camera-100.jpg")
        session = lw.NamingSession(self.folder)
        session.configure({"1": "甲", "11": "乙", "01": "丙"})
        self.assertEqual([r["code"] for r in session.mapping], ["1", "11", "01"])
        self.assertEqual(session.images[0]["code"], "")

    def test_copy_preserves_original_bytes_and_real_metadata(self):
        first = self.image("DCIM/相机001.jpg", b"photo A")
        second = self.image("DCIM/相机002.JPG", b"photo B")
        session = self.ready([("DCIM/相机001.jpg", "1", "1"), ("DCIM/相机002.JPG", "2", "")],
                             {"1": "丙环唑 100 mg/L", "2": "CK"})
        plan = session.preview()
        self.assertTrue(plan["warnings"])
        self.assertIn("mg_L", plan["rows"][0]["target"])
        result = session.apply(plan["token"])
        self.assertEqual(first.read_bytes(), b"photo A")
        self.assertEqual(second.read_bytes(), b"photo B")
        self.assertEqual(Path(result["rows"][0]["target"]).read_bytes(), b"photo A")
        with Path(result["manifest_path"]).open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["treatment"], "丙环唑 100 mg/L")
        self.assertEqual(rows[0]["replicate"], "1")
        self.assertEqual(rows[1]["replicate"], "")
        self.assertEqual(rows[1]["replicate_status"], "unknown")
        self.assertEqual(rows[0]["strain"], "层出镰刀菌")
        self.assertEqual(rows[0]["experiment_id"], "试验甲")
        # Output pictures and thumbnails never get scanned as new replicates.
        self.assertEqual(len(lw.NamingSession(self.folder).images), 2)

    def test_copy_manifest_uses_selected_diameters_and_rejects_invalid_parameters(self):
        source = self.image("A.jpg", b"A")
        session = self.ready([("A.jpg", "1", "1")])
        for dish, plug in ((float("nan"), 8), (90, float("inf")), (0, 8),
                           (90, -1), (8, 8), (6, 8), (None, 0), ("bad", None)):
            plan = session.preview()
            with self.assertRaises(ValueError):
                session.apply(plan["token"], dish_mm=dish, plug_mm=plug)
            self.assertFalse(Path(plan["output_dir"]).exists())
            self.assertEqual(source.read_bytes(), b"A")
        plan = session.preview()
        result = session.apply(plan["token"], dish_mm=90, plug_mm=8)
        with Path(result["manifest_path"]).open(encoding="utf-8-sig", newline="") as fh:
            row = next(csv.DictReader(fh))
        self.assertEqual(float(row["dish_diameter_mm"]), 90)
        self.assertEqual(float(row["plug_diameter_mm"]), 8)

    def test_rename_and_undo_preserve_every_byte_and_session(self):
        source = self.image("相机.jpg", b"distinct original")
        session = self.ready([("相机.jpg", "1", "2")])
        plan = session.preview("rename")
        result = session.apply(plan["token"], "rename", dish_mm=90, plug_mm=8)
        target = Path(plan["rows"][0]["target"])
        self.assertFalse(source.exists())
        self.assertEqual(target.read_bytes(), b"distinct original")
        restored = lw.NamingSession(self.folder)
        self.assertTrue(restored.images[0]["confirmed"])
        self.assertEqual(restored.images[0]["original_name"], "相机.jpg")
        undo = restored.undo(result["transaction_id"])
        self.assertEqual(source.read_bytes(), b"distinct original")
        self.assertFalse(target.exists())
        self.assertTrue(Path(undo["manifest_path"]).is_file())
        with Path(undo["manifest_path"]).open(encoding="utf-8-sig", newline="") as fh:
            row = next(csv.DictReader(fh))
        self.assertEqual(float(row["dish_diameter_mm"]), 90)
        self.assertEqual(float(row["plug_diameter_mm"]), 8)
        with self.assertRaises(ValueError):
            restored.undo(result["transaction_id"])

    def test_name_swap_and_undo_do_not_overwrite(self):
        self.image("甲_r1.jpg", b"source A")
        self.image("乙_r1.jpg", b"source B")
        session = self.ready([("甲_r1.jpg", "2", "1"), ("乙_r1.jpg", "1", "1")],
                             {"1": "甲", "2": "乙"}, strain="")
        plan = session.preview("rename")
        result = session.apply(plan["token"], "rename")
        self.assertEqual((self.folder / "甲_r1.jpg").read_bytes(), b"source B")
        self.assertEqual((self.folder / "乙_r1.jpg").read_bytes(), b"source A")
        session.undo(result["transaction_id"])
        self.assertEqual((self.folder / "甲_r1.jpg").read_bytes(), b"source A")
        self.assertEqual((self.folder / "乙_r1.jpg").read_bytes(), b"source B")

    def test_undo_after_mapping_edit_restores_names_without_reconfirming(self):
        source = self.image("相机.jpg", b"original")
        session = self.ready([("相机.jpg", "1", "1")])
        plan = session.preview("rename")
        result = session.apply(plan["token"], "rename")
        session.configure({"1": "新药名"}, "试验甲", "层出镰刀菌")
        undo = session.undo(result["transaction_id"])
        self.assertEqual(source.read_bytes(), b"original")
        self.assertFalse(session.images[0]["confirmed"])
        self.assertEqual(undo["manifest_path"], "")
        self.assertTrue(undo["warnings"])

    def test_failed_commit_rolls_back_without_partial_rename(self):
        a = self.image("A.jpg", b"A")
        b = self.image("B.jpg", b"B")
        session = self.ready([("A.jpg", "1", "1"), ("B.jpg", "2", "1")])
        plan = session.preview("rename")
        real_rename = os.rename
        calls = 0

        def fail_once(source, target):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise OSError("simulated second commit failure")
            return real_rename(source, target)

        with patch.object(lw.os, "rename", side_effect=fail_once):
            with self.assertRaises(OSError):
                session.apply(plan["token"], "rename")
        self.assertEqual(a.read_bytes(), b"A")
        self.assertEqual(b.read_bytes(), b"B")
        self.assertFalse(any(self.folder.glob(".naming*")))
        for row in plan["rows"]:
            self.assertFalse(Path(row["target"]).exists())

    def test_metadata_write_failure_rolls_back_rename(self):
        source = self.image("A.jpg", b"A")
        session = self.ready([("A.jpg", "1", "1")])
        plan = session.preview("rename")
        with patch.object(session, "_write_tables", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                session.apply(plan["token"], "rename")
        self.assertEqual(source.read_bytes(), b"A")
        self.assertFalse(Path(plan["rows"][0]["target"]).exists())

    def test_pending_unknown_duplicate_replicates_and_changed_files_block(self):
        self.image("A.jpg", b"A")
        self.image("B.jpg", b"B")
        session = lw.NamingSession(self.folder)
        session.configure({"1": "甲"})
        with self.assertRaises(ValueError):
            session.preview()
        with self.assertRaises(ValueError):
            session.assign(session.images[0]["id"], "11", "1")
        for image in session.images:
            session.assign(image["id"], "1", "1")
        with self.assertRaisesRegex(ValueError, "重复号"):
            session.preview()
        session.assign(session.images[1]["id"], "1", "2")
        plan = session.preview()
        (self.folder / "A.jpg").write_bytes(b"changed photo")
        with self.assertRaisesRegex(ValueError, "发生变化"):
            session.apply(plan["token"])
        self.assertFalse(Path(plan["output_dir"]).exists())

    def test_mapping_edit_and_ocr_never_silently_replace_confirmation(self):
        self.image("A.jpg", b"A")
        session = self.ready([("A.jpg", "1", "1")])
        image_id = session.images[0]["id"]
        session.suggest(image_id, "2", "9", "2-9", .99)
        self.assertEqual(session.images[0]["code"], "1")
        self.assertTrue(session.images[0]["confirmed"])
        plan = session.preview()
        session.configure({"1": "另一种药", "2": "CK"}, "试验甲", "层出镰刀菌")
        self.assertFalse(session.images[0]["confirmed"])
        with self.assertRaises(ValueError):
            session.apply(plan["token"])

    def test_user_can_clear_wrong_code_without_confirming_an_unknown_image(self):
        self.image("A.jpg", b"A")
        session = self.ready([("A.jpg", "1", "2")])
        image_id = session.images[0]["id"]
        session.assign(image_id, "", replicate="2", confirmed=False)
        image = session.images[0]
        self.assertEqual(image["code"], "")
        self.assertEqual(image["replicate"], "2")
        self.assertFalse(image["confirmed"])
        with self.assertRaises(ValueError):
            session.preview()
        with self.assertRaises(ValueError):
            session.assign(image_id, "", confirmed=True)
        restored = lw.NamingSession(self.folder)
        self.assertEqual(restored.images[0]["code"], "")
        self.assertFalse(restored.images[0]["confirmed"])

    def test_invalid_ocr_repeat_stays_unknown_and_does_not_block_next_image(self):
        self.image("A.jpg", b"A")
        self.image("B.jpg", b"B")
        session = lw.NamingSession(self.folder)
        session.configure({"1": "甲"})
        first_id, second_id = [image["id"] for image in session.images]
        session.suggest(first_id, "1", "0", ocr_text="1-0", ocr_score=.9)
        session.suggest(second_id, "1", "2", ocr_text="1-2", ocr_score=.9)
        self.assertEqual(session.images[0]["replicate"], "")
        self.assertEqual(session.images[0]["ocr_text"], "1-0")
        self.assertFalse(session.images[0]["confirmed"])
        self.assertEqual(session.images[1]["replicate"], "2")

    def test_changed_image_is_not_reconfirmed_when_restoring(self):
        source = self.image("1-1.jpg", b"A")
        session = self.ready([("1-1.jpg", "1", "1")])
        self.assertTrue(session.images[0]["confirmed"])
        source.write_bytes(b"new A")
        restored = lw.NamingSession(self.folder)
        self.assertFalse(restored.images[0]["confirmed"])

    def test_safe_filename_conversion_and_no_external_manifest_writes(self):
        self.image("A.jpg", b"A")
        session = self.ready([("A.jpg", "1", "1")], {"1": "../../药:CON?"}, strain="")
        plan = session.preview()
        target = Path(plan["rows"][0]["target"])
        target.relative_to(session.output_root)
        self.assertNotIn("/", target.name)
        self.assertNotIn(":", target.name)
        with self.assertRaises(ValueError):
            session.write_manifest(self.folder.parent / "outside")
        with self.assertRaises(ValueError):
            session.configure([{"code": "1", "treatment": "甲"}, {"code": "1", "treatment": "乙"}])

    def test_sanitized_filename_collision_is_blocked(self):
        self.image("A.jpg", b"A")
        self.image("B.jpg", b"B")
        session = self.ready([("A.jpg", "1", "1"), ("B.jpg", "2", "1")],
                             {"1": "药 10 mg/L", "2": "药 10 mg_L"}, strain="")
        with self.assertRaisesRegex(ValueError, "重复目标"):
            session.preview()


if __name__ == "__main__":
    unittest.main()
