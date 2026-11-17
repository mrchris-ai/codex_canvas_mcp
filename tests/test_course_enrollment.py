import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from canvas_mcp import server


class CourseEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.user_id = 501
        self.sis_user_id = "3330004671"
        self.course_id = 30
        self.section_id = 40
        self.account_id = 10
        self.roles = [
            {"id": 4435, "label": "TA", "base_role_type": "TaEnrollment", "workflow_state": "active"},
            {"id": 60, "label": "Student", "base_role_type": "StudentEnrollment", "workflow_state": "built_in"},
            {"id": 7777, "label": "Lab Instructor", "base_role_type": "TeacherEnrollment", "workflow_state": "active"},
            {"id": 80, "label": "Account Admin", "base_role_type": "AccountMembership", "workflow_state": "active"},
            {"id": 81, "label": "Old TA", "base_role_type": "TaEnrollment", "workflow_state": "inactive"},
        ]
        self.records = []
        self.posted = None
        self.user = {"id": self.user_id, "name": "Sample User", "sis_user_id": self.sis_user_id}
        self.target = {
            "course_id": self.course_id,
            "role_id": 4435,
            "sis_user_id": self.sis_user_id,
            "enrollment_state": "invited",
            "notify": False,
        }
        self.policy = {
            **server.disabled_policy(),
            "enabled": True,
            "approved_course_enrollment_targets": [self.target],
        }
        self.read = mock.patch.object(server, "api_get", side_effect=self.fake_read).start()
        self.list = mock.patch.object(server, "provisioning_list", side_effect=self.fake_list).start()
        self.write = mock.patch.object(server, "request_api", side_effect=self.fake_write).start()
        mock.patch.object(server, "write_policy", return_value=self.policy).start()
        mock.patch.object(server, "canvas_base_url", return_value="https://canvas.example.edu").start()
        mock.patch.object(server, "canvas_token", side_effect=AssertionError("No credential access")).start()
        self.addCleanup(mock.patch.stopall)

    def fake_read(self, path, query=None):
        return copy.deepcopy({
            f"/api/v1/courses/{self.course_id}": {
                "id": self.course_id, "account_id": self.account_id, "name": "Course",
                "workflow_state": "available",
            },
            f"/api/v1/sections/{self.section_id}": {"id": self.section_id, "course_id": self.course_id},
            f"/api/v1/users/{self.user_id}/profile": self.user,
            f"/api/v1/users/sis_user_id:{self.sis_user_id}/profile": self.user,
        }[path])

    def fake_list(self, path, query):
        if path == f"/api/v1/accounts/{self.account_id}/roles":
            self.assertTrue(query.get("show_inherited"))
            return copy.deepcopy(self.roles)
        self.assertEqual(path, f"/api/v1/courses/{self.course_id}/enrollments")
        self.assertEqual(query["user_id"], self.user_id)
        return copy.deepcopy(self.records)

    def fake_write(self, method, path, *, data):
        self.assertEqual((method, path), ("POST", f"/api/v1/courses/{self.course_id}/enrollments"))
        self.posted = data
        enrollment = data["enrollment"]
        record = {
            "id": 70,
            "course_id": self.course_id,
            "user_id": enrollment["user_id"],
            "role_id": enrollment["role_id"],
            "type": enrollment["type"],
            "enrollment_state": enrollment["enrollment_state"],
            "limit_privileges_to_course_section": enrollment["limit_privileges_to_course_section"],
            "sis_user_id": self.sis_user_id,
        }
        if "course_section_id" in enrollment:
            record["course_section_id"] = enrollment["course_section_id"]
        self.records = [record]
        return {"id": 70}

    def enroll(self, target):
        normalized = server.course_enrollment_target(target, False)
        self.policy["approved_course_enrollment_targets"] = [normalized]
        return server.enroll_user_in_course({
            **target, "confirmation": server.course_enrollment_confirmation(normalized),
        })

    def test_ta_enrollment_payload(self):
        preview = server.call_tool("canvas_preview_course_enrollment", self.target)
        self.assertEqual(preview["enrollment_type"], "TaEnrollment")
        self.assertEqual(preview["role_label"], "TA")
        self.assertEqual(preview["resolved_canvas_user_id"], self.user_id)
        self.write.assert_not_called()
        result = server.call_tool("canvas_enroll_user_in_course", {
            **self.target, "confirmation": preview["confirmation"],
        })
        self.assertEqual((result["changed"], result["verified"], result["role_id"]), (True, True, 4435))
        self.assertEqual(self.posted["enrollment"], {
            "user_id": self.user_id,
            "type": "TaEnrollment",
            "role_id": 4435,
            "enrollment_state": "invited",
            "notify": False,
            "limit_privileges_to_course_section": False,
        })

    def test_student_enrollment_payload(self):
        target = {
            "course_id": self.course_id, "role_id": 60, "canvas_user_id": self.user_id,
            "section_id": self.section_id, "enrollment_state": "active", "notify": True,
        }
        result = self.enroll(target)
        self.assertEqual(result["enrollment_type"], "StudentEnrollment")
        self.assertEqual(self.posted["enrollment"], {
            "user_id": self.user_id,
            "type": "StudentEnrollment",
            "role_id": 60,
            "enrollment_state": "active",
            "notify": True,
            "limit_privileges_to_course_section": False,
            "course_section_id": self.section_id,
        })

    def test_custom_teacher_role_uses_base_type(self):
        target = {**self.target, "role_id": 7777, "notify": True}
        self.enroll(target)
        self.assertEqual(self.posted["enrollment"]["type"], "TeacherEnrollment")
        self.assertEqual(self.posted["enrollment"]["role_id"], 7777)
        self.assertTrue(self.posted["enrollment"]["notify"])

    def test_notify_flag_changes_the_approved_target_and_payload(self):
        notified = {**self.target, "notify": True}
        self.assertNotEqual(
            server.course_enrollment_confirmation(self.target),
            server.course_enrollment_confirmation(notified),
        )
        with self.assertRaises(PermissionError):
            server.enroll_user_in_course({
                **notified, "confirmation": server.course_enrollment_confirmation(notified),
            })
        self.read.assert_not_called()
        self.write.assert_not_called()
        self.enroll(notified)
        self.assertTrue(self.posted["enrollment"]["notify"])

    def test_omitted_state_and_notify_follow_canvas_defaults(self):
        raw = {"course_id": self.course_id, "role_id": 4435, "sis_user_id": self.sis_user_id}
        normalized = server.course_enrollment_target(raw, False)
        self.assertEqual(normalized["enrollment_state"], "invited")
        self.assertFalse(normalized["notify"])
        self.assertNotIn("section_id", normalized)
        self.enroll(raw)
        self.assertFalse(self.posted["enrollment"]["notify"])
        self.assertEqual(self.posted["enrollment"]["enrollment_state"], "invited")
        self.assertNotIn("course_section_id", self.posted["enrollment"])

    def test_account_level_role_rejected(self):
        target = {**self.target, "role_id": 80}
        self.policy["approved_course_enrollment_targets"] = [target]
        with self.assertRaisesRegex(ValueError, "Account-level"):
            server.enroll_user_in_course({
                **target, "confirmation": server.course_enrollment_confirmation(target),
            })
        self.write.assert_not_called()

    def test_inactive_role_rejected(self):
        target = {**self.target, "role_id": 81}
        self.policy["approved_course_enrollment_targets"] = [target]
        with self.assertRaisesRegex(ValueError, "not an active"):
            server.preview_course_enrollment(target)
        self.write.assert_not_called()

    def test_missing_allowlist_and_disabled_policy_block_before_network(self):
        confirmation = server.course_enrollment_confirmation(self.target)
        self.policy["approved_course_enrollment_targets"] = []
        with self.assertRaises(PermissionError):
            server.enroll_user_in_course({**self.target, "confirmation": confirmation})
        self.policy["enabled"] = False
        self.policy["approved_course_enrollment_targets"] = [self.target]
        with self.assertRaises(PermissionError):
            server.enroll_user_in_course({**self.target, "confirmation": confirmation})
        self.read.assert_not_called()
        self.list.assert_not_called()
        self.write.assert_not_called()

    def test_wrong_confirmation_blocks_before_network(self):
        with self.assertRaises(PermissionError):
            server.enroll_user_in_course({**self.target, "confirmation": "APPROVE CANVAS COURSE ENROLLMENT " + ("ab" * 32)})
        self.read.assert_not_called()
        self.write.assert_not_called()

    def test_mismatched_identity_and_missing_user_are_rejected(self):
        with self.assertRaises(ValueError):
            server.course_enrollment_target({"course_id": self.course_id, "role_id": 4435}, False)
        mismatched = {**self.target, "canvas_user_id": self.user_id, "sis_user_id": "0000000000"}
        self.policy["approved_course_enrollment_targets"] = [server.course_enrollment_target(mismatched, False)]
        with self.assertRaisesRegex(ValueError, "SIS user"):
            server.preview_course_enrollment(mismatched)
        self.write.assert_not_called()

    def test_identical_enrollment_is_noop_and_state_conflict_does_not_write(self):
        self.records = [{
            "id": 70, "course_id": self.course_id, "user_id": self.user_id, "role_id": 4435,
            "type": "TaEnrollment", "enrollment_state": "invited",
            "limit_privileges_to_course_section": False, "sis_user_id": self.sis_user_id,
        }]
        result = self.enroll(self.target)
        self.assertFalse(result["changed"])
        self.write.assert_not_called()
        self.records[0]["enrollment_state"] = "inactive"
        with self.assertRaisesRegex(ValueError, "requested state"):
            self.enroll(self.target)
        self.write.assert_not_called()

    def test_read_back_failure_does_not_retry(self):
        self.write.side_effect = lambda method, path, *, data: {"id": 71}
        with self.assertRaisesRegex(RuntimeError, "Do not automatically retry"):
            self.enroll(self.target)
        self.assertEqual(self.write.call_count, 1)

    def test_tool_contract(self):
        tools = {tool["name"]: tool for tool in server.TOOLS}
        preview = tools["canvas_preview_course_enrollment"]
        mutation = tools["canvas_enroll_user_in_course"]
        self.assertTrue(preview["annotations"]["readOnlyHint"])
        self.assertFalse(preview["annotations"].get("destructiveHint", False))
        self.assertFalse(mutation["annotations"]["readOnlyHint"])
        self.assertTrue(mutation["annotations"]["destructiveHint"])
        self.assertFalse(mutation["inputSchema"]["additionalProperties"])
        self.assertEqual(mutation["inputSchema"]["required"], ["course_id", "role_id", "confirmation"])
        self.assertEqual(
            set(mutation["inputSchema"]["properties"]),
            {"course_id", "role_id", "canvas_user_id", "sis_user_id", "section_id",
             "enrollment_state", "notify", "confirmation"},
        )
        instructions = server.handle_request({"id": 1, "method": "initialize"})["result"]["instructions"]
        self.assertIn("approved_course_enrollment_targets", instructions)


class CourseEnrollmentPolicyTests(unittest.TestCase):
    def setUp(self):
        self.target = server.course_enrollment_target({
            "course_id": 30, "role_id": 4435, "sis_user_id": "3330004671",
            "enrollment_state": "invited", "notify": False,
        }, False)
        self.confirmation = server.course_enrollment_confirmation(self.target)

    def write_policy_file(self, directory, policy):
        path = Path(directory) / "policy.json"
        path.write_text(json.dumps(policy))
        path.chmod(0o600)
        return path

    def legacy_policy(self, version):
        policy = {"version": version, "enabled": True, "approved_course_ids": []}
        if version >= 2:
            policy["approved_rubric_delete_course_ids"] = []
        if version >= 3:
            policy["approved_student_deactivation_course_ids"] = []
        if version >= 4:
            policy.update({
                "approved_course_creation_account_ids": [],
                "approved_instructor_enrollment_course_ids": [],
                "approved_instructor_role_ids": [],
            })
        if version >= 5:
            policy.update({
                "approved_course_creation_targets": [],
                "approved_instructor_enrollment_targets": [],
            })
        if version >= 6:
            policy["approved_user_update_targets"] = []
        if version == 7:
            policy["approved_student_enrollment_targets"] = []
        if version >= 9:
            policy["approved_user_creation_fingerprints"] = ["a" * 64]
        return policy

    def test_old_versions_stay_readable_and_grant_no_course_enrollment(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"CANVAS_WRITE_POLICY": str(Path(directory) / "policy.json")}):
                for version in (1, 5, 7, 8, 9):
                    self.write_policy_file(directory, self.legacy_policy(version))
                    loaded = server.write_policy()
                    self.assertEqual(loaded["approved_course_enrollment_targets"], [])
                    self.assertEqual(loaded["version"], 10)
                    if version >= 9:
                        self.assertEqual(loaded["approved_user_creation_fingerprints"], ["a" * 64])
                    else:
                        self.assertEqual(loaded["approved_user_creation_fingerprints"], [])
                    with mock.patch.object(server, "api_get") as read, \
                            mock.patch.object(server, "request_api") as write:
                        with self.assertRaises(PermissionError):
                            server.enroll_user_in_course({**self.target, "confirmation": self.confirmation})
                        read.assert_not_called()
                        write.assert_not_called()

    def test_version_9_cannot_carry_the_new_key_and_version_10_requires_it(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"CANVAS_WRITE_POLICY": str(Path(directory) / "policy.json")}):
                stuffed = self.legacy_policy(9)
                stuffed["approved_course_enrollment_targets"] = [self.target]
                self.write_policy_file(directory, stuffed)
                with self.assertRaises(server.ConfigurationError):
                    server.write_policy()
                current = {**server.disabled_policy(), "enabled": True,
                           "approved_course_enrollment_targets": [self.target]}
                self.write_policy_file(directory, current)
                self.assertEqual(server.write_policy(), current)
                missing = {key: value for key, value in current.items()
                           if key != "approved_course_enrollment_targets"}
                self.write_policy_file(directory, missing)
                with self.assertRaises(server.ConfigurationError):
                    server.write_policy()

    def test_invalid_targets_fail_closed(self):
        valid = self.target
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"CANVAS_WRITE_POLICY": str(Path(directory) / "policy.json")}):
                for bad in (
                    [valid, valid],
                    [{**valid, "notify": "yes"}],
                    [{**valid, "role_id": True}],
                    [{key: value for key, value in valid.items() if key != "notify"}],
                    [{**valid, "enrollment_state": "deleted"}],
                    [{**valid, "extra": 1}],
                    [{key: value for key, value in valid.items() if key != "sis_user_id"}],
                    "nope",
                ):
                    policy = {**server.disabled_policy(), "approved_course_enrollment_targets": bad}
                    self.write_policy_file(directory, policy)
                    with self.subTest(bad=bad), self.assertRaises(server.ConfigurationError):
                        server.write_policy()

    def test_enabled_confirmation_without_allowlist_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = {**server.disabled_policy(), "enabled": True, "approved_course_enrollment_targets": []}
            path = self.write_policy_file(directory, policy)
            with mock.patch.dict(os.environ, {"CANVAS_WRITE_POLICY": str(path)}), \
                    mock.patch.object(server, "api_get") as read, \
                    mock.patch.object(server, "request_api") as write:
                with self.assertRaises(PermissionError):
                    server.enroll_user_in_course({**self.target, "confirmation": self.confirmation})
                read.assert_not_called()
                write.assert_not_called()
