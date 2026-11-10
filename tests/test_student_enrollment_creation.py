import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from canvas_mcp import server

REAL_WRITE_POLICY = server.write_policy


class StudentEnrollmentCreationTests(unittest.TestCase):
    def setUp(self):
        self.target = {
            "course_id": 30, "section_id": 40, "canvas_user_id": 50,
            "sis_user_id": "1234567890", "sis_course_id": "SIS_30", "role_id": 60,
        }
        self.record = {
            "id": 70, "course_id": 30, "course_section_id": 40, "user_id": 50,
            "sis_user_id": "1234567890", "sis_course_id": "SIS_30",
            "role_id": 60, "role": "StudentEnrollment", "type": "StudentEnrollment",
            "limit_privileges_to_course_section": False, "enrollment_state": "invited",
        }
        self.records = [copy.deepcopy(self.record)]
        self.policy = {**server.disabled_policy(), "enabled": True}
        self.read = mock.patch.object(server, "api_get", side_effect=self.fake_read).start()
        self.list = mock.patch.object(server, "provisioning_list", side_effect=self.fake_list).start()
        self.write = mock.patch.object(server, "request_api", side_effect=self.fake_write).start()
        mock.patch.object(server, "write_policy", return_value=self.policy).start()
        mock.patch.object(server, "canvas_base_url", return_value="https://canvas.example.edu").start()
        mock.patch.object(server, "canvas_token", side_effect=AssertionError("No credential access")).start()
        self.addCleanup(mock.patch.stopall)

    def fake_read(self, path):
        return copy.deepcopy({
            "/api/v1/courses/30": {"id": 30, "account_id": 10, "sis_course_id": "SIS_30",
                                   "workflow_state": "available", "name": "Course"},
            "/api/v1/sections/40": {"id": 40, "course_id": 30},
            "/api/v1/users/50/profile": {"id": 50, "sis_user_id": "1234567890", "name": "Student"},
        }[path])

    def fake_list(self, path, query):
        if path == "/api/v1/accounts/10/roles":
            return [{"id": 60, "role": "StudentEnrollment", "label": "Student",
                     "base_role_type": "StudentEnrollment", "workflow_state": "built_in"}]
        self.assertEqual(path, "/api/v1/courses/30/enrollments")
        self.assertEqual(query["user_id"], 50)
        return copy.deepcopy(self.records)

    def fake_write(self, method, path, *, data):
        self.assertEqual((method, path), ("POST", "/api/v1/courses/30/enrollments"))
        self.assertEqual(data["enrollment"]["type"], "StudentEnrollment")
        self.assertEqual(data["enrollment"]["course_section_id"], 40)
        self.assertEqual(data["enrollment"]["notify"], True)
        self.records = [copy.deepcopy(self.record)]
        return {"id": 70}

    def test_existing_invitation_is_noop(self):
        preview = server.preview_student_enrollment(self.target)
        self.assertEqual(preview["existing_enrollment"], 70)
        result = server.enroll_student({**self.target, "confirmation": preview["confirmation"]})
        self.assertEqual((result["changed"], result["enrollment_id"], result["enrollment_state"]),
                         (False, 70, "invited"))
        self.write.assert_not_called()

    def test_new_invitation_is_verified(self):
        self.records = []
        preview = server.preview_student_enrollment(self.target)
        result = server.enroll_student({**self.target, "confirmation": preview["confirmation"]})
        self.assertEqual((result["changed"], result["verified"]), (True, True))
        self.write.assert_called_once()

    def test_exact_deleted_enrollment_can_be_restored(self):
        self.records = [{**self.record, "enrollment_state": "deleted"}]
        preview = server.preview_student_enrollment(self.target)
        self.assertEqual((preview["existing_enrollment"], preview["existing_state"]), (70, "deleted"))
        result = server.enroll_student({**self.target, "confirmation": preview["confirmation"]})
        self.assertEqual((result["changed"], result["verified"], result["enrollment_state"]),
                         (True, True, "invited"))
        self.write.assert_called_once()

    def test_new_invitation_may_coexist_with_exact_deleted_history(self):
        old = {**self.record, "enrollment_state": "deleted"}
        self.records = [old]

        def create_new(method, path, *, data):
            self.assertEqual((method, path), ("POST", "/api/v1/courses/30/enrollments"))
            self.records = [old, {**self.record, "id": 71}]
            return {"id": 71}

        self.write.side_effect = create_new
        result = server.enroll_student({**self.target,
                                        "confirmation": server.student_enrollment_confirmation(self.target)})
        self.assertEqual((result["enrollment_id"], result["enrollment_state"]), (71, "invited"))

    def test_deleted_enrollment_with_a_different_target_still_blocks(self):
        self.records = [{**self.record, "enrollment_state": "deleted", "course_section_id": 41}]
        with self.assertRaises(ValueError):
            server.preview_student_enrollment(self.target)
        self.write.assert_not_called()

    def test_unexpected_read_back_after_deleted_record_never_retries(self):
        self.records = [{**self.record, "enrollment_state": "deleted"}]

        def no_change(method, path, *, data):
            return {"id": 70}

        self.write.side_effect = no_change
        with self.assertRaisesRegex(RuntimeError, "needs inspection"):
            server.enroll_student({**self.target,
                                   "confirmation": server.student_enrollment_confirmation(self.target)})
        self.write.assert_called_once()

    def test_enabled_legacy_policy_invites_without_student_target(self):
        self.records = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.touch(mode=0o600)
            policy = {key: value for key, value in server.disabled_policy().items()
                      if key not in {"approved_user_update_targets", "approved_user_creation_fingerprints",
                                     "approved_sis_enrollment_import_fingerprints"}}
            policy.update(version=5, enabled=True)
            path.write_text(json.dumps(policy))
            with mock.patch.dict(os.environ, {"CANVAS_WRITE_POLICY": str(path)}), \
                 mock.patch.object(server, "write_policy", side_effect=REAL_WRITE_POLICY):
                result = server.enroll_student({**self.target,
                                                "confirmation": server.student_enrollment_confirmation(self.target)})
        self.assertTrue(result["verified"])
        self.write.assert_called_once()

    def test_conflict_or_disabled_policy_never_writes(self):
        confirmation = server.student_enrollment_confirmation(self.target)
        for change in ({"role_id": 61}, {"enrollment_state": "inactive"},
                       {"course_section_id": 41}):
            with self.subTest(change=change):
                self.records = [{**self.record, **change}]
                with self.assertRaises(ValueError):
                    server.enroll_student({**self.target, "confirmation": confirmation})
        self.records = []
        self.policy["enabled"] = False
        with self.assertRaises(PermissionError):
            server.enroll_student({**self.target, "confirmation": confirmation})
        self.write.assert_not_called()

    def test_exact_target_and_confirmation(self):
        confirmation = server.student_enrollment_confirmation(self.target)
        with self.assertRaises(PermissionError):
            server.enroll_student({**self.target, "confirmation": "wrong"})
        with self.assertRaises(ValueError):
            server.preview_student_enrollment({**self.target, "extra": 1})
        self.assertEqual(server.call_tool("canvas_preview_student_enrollment", self.target)["confirmation"], confirmation)
        tools = {item["name"]: item for item in server.TOOLS}
        self.assertTrue(tools["canvas_preview_student_enrollment"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["canvas_enroll_student"]["annotations"]["readOnlyHint"])
        self.write.assert_not_called()

    def test_version_8_needs_no_student_target_and_version_7_is_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.touch(mode=0o600)
            with mock.patch.dict(os.environ, {"CANVAS_WRITE_POLICY": str(path)}):
                current = {**server.disabled_policy(), "enabled": True}
                path.write_text(json.dumps(current))
                self.assertEqual(REAL_WRITE_POLICY(), current)
                version_8 = {key: value for key, value in current.items()
                             if key not in {"approved_user_creation_fingerprints", "approved_sis_enrollment_import_fingerprints"}}
                path.write_text(json.dumps({**version_8, "version": 8}))
                self.assertEqual(REAL_WRITE_POLICY(), current)
                legacy = {**version_8, "version": 7,
                          "approved_student_enrollment_targets": [self.target]}
                path.write_text(json.dumps(legacy))
                self.assertEqual(REAL_WRITE_POLICY(), current)
                for bad in ([self.target, self.target], [{**self.target, "role_id": True}],
                            [{**self.target, "extra": 1}]):
                    path.write_text(json.dumps({**legacy, "approved_student_enrollment_targets": bad}))
                    with self.subTest(bad=bad), self.assertRaises(server.ConfigurationError):
                        REAL_WRITE_POLICY()
                path.write_text(json.dumps({**current, "approved_student_enrollment_targets": []}))
                with self.assertRaises(server.ConfigurationError):
                    REAL_WRITE_POLICY()


if __name__ == "__main__":
    unittest.main()
