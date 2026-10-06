import copy
import hashlib
import unittest
from unittest import mock

from canvas_mcp import server


class SyllabusTests(unittest.TestCase):
    def setUp(self):
        self.before = {"id": 123, "syllabus_body": "<p>Before</p>",
                       "workflow_state": "unpublished", "blueprint": True}
        self.args = {"course_id": 123, "syllabus_body": "<p>After</p>",
                     "expected_sha256": hashlib.sha256(b"<p>Before</p>").hexdigest(),
                     "confirmation": "APPROVE CANVAS SYLLABUS WRITE course 123"}
        self.policy = mock.patch.object(server, "write_policy", return_value={
            "enabled": True, "approved_course_ids": [123]})
        self.policy.start()
        self.addCleanup(self.policy.stop)

    def test_preview_defaults_to_no_write_and_returns_recovery_body(self):
        with mock.patch.object(server, "api_get", return_value=self.before), mock.patch.object(server, "request_api") as write:
            result = server.call_tool("canvas_update_syllabus", self.args)
            self.assertTrue(result["dry_run"])
            self.assertEqual(result["previous_syllabus_body"], self.before["syllabus_body"])
            write.assert_not_called()

    def test_exact_payload_and_readback(self):
        saved = {**self.before, "syllabus_body": self.args["syllabus_body"]}
        with mock.patch.object(server, "api_get", side_effect=[self.before, saved]) as read, mock.patch.object(server, "request_api") as write:
            result = server.call_tool("canvas_update_syllabus", {**self.args, "dry_run": False})
            write.assert_called_once_with("PUT", "/api/v1/courses/123", data={"course": {"syllabus_body": "<p>After</p>"}})
            self.assertEqual(read.call_count, 2)
            self.assertTrue(result["verified"])

    def test_rejects_policy_confirmation_and_cross_course_before_network(self):
        for change in ({"confirmation": "yes"}, {"course_id": 456, "confirmation": "APPROVE CANVAS SYLLABUS WRITE course 456"}):
            with self.subTest(change=change), mock.patch.object(server, "api_get") as read, mock.patch.object(server, "request_api") as write:
                with self.assertRaises(PermissionError):
                    server.update_syllabus({**self.args, **change})
                read.assert_not_called()
                write.assert_not_called()
        with mock.patch.object(server, "write_policy", return_value=server.disabled_policy()), mock.patch.object(server, "api_get") as read:
            with self.assertRaises(PermissionError):
                server.update_syllabus(self.args)
            read.assert_not_called()

    def test_rejects_extra_fields_and_invalid_types(self):
        for change in ({"course": {"event": "offer"}}, {"event": "offer"}, {"path": "/api/v1/courses/456"}, {"course_id": True}, {"dry_run": "false"}, {"syllabus_body": " "}, {"syllabus_body": None}, {"expected_sha256": "bad"}):
            with self.subTest(change=change), mock.patch.object(server, "api_get") as read, mock.patch.object(server, "request_api") as write:
                with self.assertRaises(ValueError):
                    server.update_syllabus({**self.args, **change})
                read.assert_not_called()
                write.assert_not_called()

    def test_rejects_stale_or_wrong_course_before_write(self):
        for change in ({"syllabus_body": "Changed"}, {"id": 456}, {"syllabus_body": None}):
            with self.subTest(change=change), mock.patch.object(server, "api_get", return_value={**self.before, **change}), mock.patch.object(server, "request_api") as write:
                with self.assertRaises(ValueError):
                    server.update_syllabus({**self.args, "dry_run": False})
                write.assert_not_called()

    def test_does_not_claim_success_if_readback_or_settings_differ(self):
        for change in ({"syllabus_body": "Wrong"}, {"workflow_state": "available"}, {"id": 456}):
            saved = {**self.before, "syllabus_body": self.args["syllabus_body"], **change}
            with self.subTest(change=change), mock.patch.object(server, "api_get", side_effect=[copy.deepcopy(self.before), saved]), mock.patch.object(server, "request_api") as write:
                with self.assertRaises(RuntimeError):
                    server.update_syllabus({**self.args, "dry_run": False})
                self.assertEqual(write.call_count, 1)

    def test_tool_schema_is_narrow_and_mutating(self):
        tool = next(t for t in server.TOOLS if t["name"] == "canvas_update_syllabus")
        self.assertFalse(tool["inputSchema"]["additionalProperties"])
        self.assertFalse(tool["annotations"]["readOnlyHint"])
        self.assertTrue(tool["annotations"]["destructiveHint"])

