"""Tests for the NotebookLM workflow ensuring only courses with new files create folders,
and previously delivered files are never re-created even when deleted on Drive.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import notebooklm_export as nblm


class NotebookLMWorkflowTests(unittest.TestCase):
    def test_exercise_course_is_queued_only_with_main_lecture(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            lecture = staging / "Strafrecht I__3723286"
            exercises = staging / "Übungen Strafrecht I__3723279"
            lecture.mkdir()
            exercises.mkdir()
            (lecture / "Skript.pdf").write_bytes(b"lecture")
            (exercises / "Fall 1.pdf").write_bytes(b"exercise")
            config = {
                "courses": [
                    {
                        "ref_id": 3723286,
                        "name": "Strafrecht I",
                        "notebooklm_extra_sources": ["Übungen Strafrecht I__3723279"],
                    },
                    {"ref_id": 3723279, "name": "Übungen Strafrecht I"},
                ]
            }

            self.assertEqual(nblm.build(staging, config), 0)

            lecture_files = sorted(
                p.name for p in (lecture / "NotebookLM").iterdir()
                if p.is_file() and not p.name.startswith(".")
            )
            self.assertEqual(len(lecture_files), 2)
            self.assertTrue(any(name.startswith("Übungen__") for name in lecture_files))
            self.assertFalse((exercises / "NotebookLM").exists())

    def test_only_new_files_are_queued_and_never_re_delivered(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            
            # Setup two courses
            course_a = staging / "Strafrecht I__3723286"
            course_b = staging / "Privatrecht I__3721432"
            course_a.mkdir()
            course_b.mkdir()
            
            config = {
                "courses": [
                    {"ref_id": 3723286, "name": "Strafrecht I"},
                    {"ref_id": 3721432, "name": "Privatrecht I"},
                ]
            }
            
            # --- DAY 1: Course A has 2 new PDFs, Course B has 0 ---
            file1 = course_a / "3784400__Skript_1.pdf"
            file2 = course_a / "3784401__Uebung_1.pdf"
            file1.write_bytes(b"%PDF-1.4 Content of Skript 1")
            file2.write_bytes(b"%PDF-1.4 Content of Uebung 1")
            
            # Build NotebookLM queue
            result = nblm.build(staging, config)
            self.assertEqual(result, 0)
            
            # Course A should have NotebookLM folder with 2 files
            queue_a = course_a / "NotebookLM"
            self.assertTrue(queue_a.is_dir())
            files_a = [p for p in queue_a.iterdir() if p.is_file() and not p.name.startswith(".")]
            self.assertEqual(len(files_a), 2)
            
            # Course B has NO new files -> NotebookLM folder should NOT exist!
            queue_b = course_b / "NotebookLM"
            self.assertFalse(queue_b.exists())
            
            # Simulate server_sync check: only courses with files_to_copy are processed
            copied_courses = []
            for c_folder in sorted(path for path in staging.iterdir() if path.is_dir()):
                src = c_folder / "NotebookLM"
                if not src.is_dir():
                    continue
                files_to_copy = [p for p in src.iterdir() if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".tmp")]
                if files_to_copy:
                    copied_courses.append(c_folder.name)
            
            self.assertEqual(copied_courses, ["Strafrecht I__3723286"])
            
            # Acknowledge delivery
            delivered = nblm.acknowledge(staging)
            self.assertEqual(delivered, 2)
            
            # After acknowledge, the local queue folder must be cleanly removed
            self.assertFalse(queue_a.exists())
            
            # Ledger must record the delivery
            ledger_a = json.loads((course_a / ".notebooklm-export-index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(ledger_a), 2)
            
            # --- DAY 2: User deleted folder on Drive, NO new files on ILIAS ---
            # Run build again on day 2
            result_day2 = nblm.build(staging, config)
            self.assertEqual(result_day2, 0)
            
            # Neither Course A nor Course B should have a NotebookLM folder!
            self.assertFalse(queue_a.exists(), "Course A folder must NOT be re-created!")
            self.assertFalse(queue_b.exists(), "Course B folder must NOT be created!")
            
            # Check server_sync simulation on Day 2
            day2_copied = []
            for c_folder in sorted(path for path in staging.iterdir() if path.is_dir()):
                src = c_folder / "NotebookLM"
                if not src.is_dir():
                    continue
                files_to_copy = [p for p in src.iterdir() if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".tmp")]
                if files_to_copy:
                    day2_copied.append(c_folder.name)
            
            self.assertEqual(day2_copied, [], "Day 2 must have ZERO courses queued for upload!")
            
            # --- DAY 3: Lecturer uploads 1 new file to Course B ---
            file_b1 = course_b / "3790000__Privatrecht_Skript.pdf"
            file_b1.write_bytes(b"%PDF-1.4 Content of Privatrecht Skript")
            
            result_day3 = nblm.build(staging, config)
            self.assertEqual(result_day3, 0)
            
            # Course A must still be empty!
            self.assertFalse(queue_a.exists(), "Course A must remain empty on Day 3")
            # Course B must now have its folder with 1 file!
            self.assertTrue(queue_b.is_dir(), "Course B must now have a NotebookLM folder!")
            files_b = [p for p in queue_b.iterdir() if p.is_file() and not p.name.startswith(".")]
            self.assertEqual(len(files_b), 1)
            
            day3_copied = []
            for c_folder in sorted(path for path in staging.iterdir() if path.is_dir()):
                src = c_folder / "NotebookLM"
                if not src.is_dir():
                    continue
                files_to_copy = [p for p in src.iterdir() if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".tmp")]
                if files_to_copy:
                    day3_copied.append(c_folder.name)
            
            self.assertEqual(day3_copied, ["Privatrecht I__3721432"], "Day 3 must only upload Course B!")
            
            # Acknowledge Day 3
            delivered_day3 = nblm.acknowledge(staging)
            self.assertEqual(delivered_day3, 1)
            self.assertFalse(queue_b.exists())


if __name__ == "__main__":
    unittest.main()
