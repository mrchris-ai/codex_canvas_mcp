from pathlib import Path
import json
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from approve_content_course import approve, ConfigurationError


class CourseApprovalTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "policy.json"
        self.raw = dict(version=2, enabled=True, approved_course_ids=[101],
                        approved_rubric_delete_course_ids=[303])
        self.write()

    def write(self):
        self.path.write_text(json.dumps(self.raw))
        self.path.chmod(0o600)

    def test_add_and_repeat_preserve_other_grants_and_permissions(self):
        self.assertTrue(approve(self.path, 202))
        self.assertFalse(approve(self.path, 202))
        self.assertEqual(json.loads(self.path.read_text()), dict(self.raw, approved_course_ids=[101, 202]))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_refuse_disabled_policy(self):
        self.raw["enabled"] = False
        self.write()
        with self.assertRaises(ConfigurationError):
            approve(self.path, 202)
        self.assertEqual(json.loads(self.path.read_text()), self.raw)

    def test_refuse_public_or_linked_policy(self):
        linked = self.path.with_name("linked")
        linked.symlink_to(self.path)
        with self.assertRaises(ConfigurationError):
            approve(linked, 202)
        self.path.chmod(0o644)
        with self.assertRaises(ConfigurationError):
            approve(self.path, 202)

    def test_missing_or_invalid_id(self):
        with self.assertRaises(ValueError):
            approve(self.path, -1)
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            approve(self.path, 202)
        self.assertFalse(self.path.exists())
