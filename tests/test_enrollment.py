import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from canvas_mcp import server


class StudentDeactivationTests(unittest.TestCase):
    def setUp(self):
        self.target = {
            "course_id": 123,
            "section_id": 456,
            "enrollment_id": 789,
            "canvas_user_id": 321,
            "sis_user_id": "0000000321",
        }
        self.confirmation = "APPROVE CANVAS STUDENT DEACTIVATION course 123 enrollment 789"
        self.active = {
            "id": 789,
            "course_id": 123,
            "course_section_id": 456,
            "user_id": 321,
            "sis_user_id": "0000000321",
            "type": "StudentEnrollment",
            "enrollment_state": "active",
        }
        self.policy = {
            "version": 3,
            "enabled": True,
            "approved_course_ids": [],
            "approved_rubric_delete_course_ids": [],
            "approved_student_deactivation_course_ids": [123],
        }

    def test_policy_versions_and_separate_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text("{}")
            path.chmod(0o600)
            with mock.patch.dict(os.environ, {"CANVAS_WRITE_POLICY": str(path)}):
                for version, extra in (
                    (2, {"approved_rubric_delete_course_ids": []}),
                    (3, {"approved_rubric_delete_course_ids": [], "approved_student_deactivation_course_ids": [123]}),
                ):
                    path.write_text(json.dumps({"version": version, "enabled": True, "approved_course_ids": [], **extra}))
                    result = server.write_policy()
                    self.assertEqual(result["approved_student_deactivation_course_ids"], [123] if version == 3 else [])
                    with mock.patch.object(server, "write_policy", return_value=result):
                        if version == 2:
                            with self.assertRaises(PermissionError):
                                server.require_student_deactivation_approval(123, 789, self.confirmation)
                        else:
                            server.require_student_deactivation_approval(123, 789, self.confirmation)
                for bad in ([True], [0], [123, 123], "123"):
                    path.write_text(json.dumps({**self.policy, "approved_student_deactivation_course_ids": bad}))
                    with self.subTest(bad=bad), self.assertRaises(server.ConfigurationError):
                        server.write_policy()

    def test_preview_is_read_only_and_shows_live_state(self):
        with mock.patch.object(server, "api_get", return_value=[self.active]) as read, mock.patch.object(server, "request_api") as write:
            result = server.call_tool("canvas_preview_student_deactivation", self.target)
            self.assertTrue(result["can_deactivate"])
            self.assertEqual(result["current_state"], "active")
            read.assert_called_once_with(
                "/api/v1/courses/123/enrollments",
                {"user_id": 321, "state[]": ["active", "invited", "inactive"], "per_page": 100},
            )
            write.assert_not_called()

    def test_write_uses_only_exact_deactivation_and_verifies(self):
        inactive = {**self.active, "enrollment_state": "inactive"}
        with mock.patch.object(server, "write_policy", return_value=self.policy), mock.patch.object(
            server, "api_get", side_effect=[[self.active], [inactive]]
        ) as read, mock.patch.object(server, "request_api", return_value=inactive) as write:
            result = server.call_tool(
                "canvas_deactivate_student_enrollment",
                {**self.target, "confirmation": self.confirmation},
            )
            write.assert_called_once_with(
                "DELETE", "/api/v1/courses/123/enrollments/789", query={"task": "deactivate"}
            )
            self.assertEqual(read.call_count, 2)
            self.assertEqual(result, {"changed": True, "verified": True, "enrollment_state": "inactive", "enrollment_id": 789})

    def test_policy_and_exact_confirmation_block_network(self):
        for policy, confirmation in (
            ({**self.policy, "approved_student_deactivation_course_ids": []}, self.confirmation),
            ({**self.policy, "approved_course_ids": [123], "approved_student_deactivation_course_ids": []}, self.confirmation),
            ({**self.policy, "enabled": False}, self.confirmation),
            (self.policy, "yes"),
            (self.policy, "APPROVE CANVAS STUDENT DEACTIVATION course 123 enrollment 790"),
        ):
            with self.subTest(policy=policy, confirmation=confirmation), mock.patch.object(
                server, "write_policy", return_value=policy
            ), mock.patch.object(server, "api_get") as read, mock.patch.object(server, "request_api") as write:
                with self.assertRaises(PermissionError):
                    server.deactivate_student_enrollment({**self.target, "confirmation": confirmation})
                read.assert_not_called()
                write.assert_not_called()

    def test_wrong_identity_and_unsupported_state_block_write(self):
        for change in (
            {"course_id": 999}, {"course_section_id": 999}, {"user_id": 999},
            {"sis_user_id": "someone-else"}, {"type": "TeacherEnrollment"},
            {"enrollment_state": "invited"},
        ):
            with self.subTest(change=change), mock.patch.object(server, "write_policy", return_value=self.policy), mock.patch.object(
                server, "api_get", return_value=[{**self.active, **change}]
            ), mock.patch.object(server, "request_api") as write:
                with self.assertRaises(ValueError):
                    server.deactivate_student_enrollment({**self.target, "confirmation": self.confirmation})
                write.assert_not_called()

    def test_already_inactive_is_verified_no_change(self):
        with mock.patch.object(server, "write_policy", return_value=self.policy), mock.patch.object(
            server, "api_get", return_value=[{**self.active, "enrollment_state": "inactive"}]
        ), mock.patch.object(server, "request_api") as write:
            result = server.deactivate_student_enrollment({**self.target, "confirmation": self.confirmation})
            self.assertEqual(result["changed"], False)
            self.assertEqual(result["enrollment_state"], "inactive")
            write.assert_not_called()

    def test_bad_arguments_and_incomplete_list_fail_closed(self):
        for change in ({"section_id": True}, {"sis_user_id": " "}, {"path": "/api/v1/courses/999"}):
            with self.subTest(change=change), mock.patch.object(server, "api_get") as read:
                with self.assertRaises(ValueError):
                    server.preview_student_deactivation({**self.target, **change})
                read.assert_not_called()
        with mock.patch.object(server, "api_get", return_value=[self.active] * 100):
            with self.assertRaises(RuntimeError):
                server.preview_student_deactivation(self.target)

    def test_readback_failure_does_not_claim_success(self):
        with mock.patch.object(server, "write_policy", return_value=self.policy), mock.patch.object(
            server, "api_get", side_effect=[[self.active], [{**self.active, "enrollment_state": "active"}]]
        ), mock.patch.object(server, "request_api") as write:
            with self.assertRaisesRegex(RuntimeError, "Inspect the roster"):
                server.deactivate_student_enrollment({**self.target, "confirmation": self.confirmation})
            self.assertEqual(write.call_count, 1)

    def test_tool_contracts_are_narrow(self):
        tools = {tool["name"]: tool for tool in server.TOOLS}
        preview = tools["canvas_preview_student_deactivation"]
        mutation = tools["canvas_deactivate_student_enrollment"]
        self.assertTrue(preview["annotations"]["readOnlyHint"])
        self.assertFalse(mutation["annotations"]["readOnlyHint"])
        self.assertTrue(mutation["annotations"]["destructiveHint"])
        self.assertFalse(mutation["inputSchema"]["additionalProperties"])
        self.assertEqual(
            set(mutation["inputSchema"]["properties"]),
            set(self.target) | {"confirmation"},
        )
