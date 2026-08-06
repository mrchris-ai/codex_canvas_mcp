import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from canvas_mcp import server


class ServerSafetyTests(unittest.TestCase):
    def test_write_is_disabled_without_policy_path(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(server.write_policy(), server.disabled_policy())
            with self.assertRaises(PermissionError):
                server.require_write_approval(123, "APPROVE CANVAS PAGE WRITE course 123", "PAGE")

    def test_policy_requires_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(
                json.dumps({"version": 1, "enabled": True, "approved_course_ids": [123]}),
                encoding="utf-8",
            )
            path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
            with mock.patch.dict(os.environ, {"CANVAS_WRITE_POLICY": str(path)}, clear=True):
                with self.assertRaises(server.ConfigurationError):
                    server.write_policy()

    def test_exact_confirmation_and_allowlist_are_required(self):
        policy = {"version": 1, "enabled": True, "approved_course_ids": [123]}
        with mock.patch.object(server, "write_policy", return_value=policy):
            with self.assertRaises(PermissionError):
                server.require_write_approval(456, "APPROVE CANVAS PAGE WRITE course 456", "PAGE")
            with self.assertRaises(PermissionError):
                server.require_write_approval(123, "yes", "PAGE")
            server.require_write_approval(123, "APPROVE CANVAS PAGE WRITE course 123", "PAGE")

    def test_read_tool_uses_get_only(self):
        with mock.patch.object(server, "api_get", return_value={"ok": True}) as api_get:
            result = server.call_tool("canvas_read_api", {"path": "/api/v1/courses/1"})
        self.assertEqual(result, {"ok": True})
        api_get.assert_called_once_with("/api/v1/courses/1", None)

    def test_page_write_targets_only_page_endpoint(self):
        args = {
            "course_id": 123,
            "title": "Safe page",
            "body": "<p>Body</p>",
            "confirmation": "APPROVE CANVAS PAGE WRITE course 123",
        }
        with (
            mock.patch.object(server, "require_write_approval"),
            mock.patch.object(server, "request_api", return_value={"ok": True}) as request_api,
        ):
            server.call_tool("canvas_write_page", args)
        request_api.assert_called_once_with(
            "POST",
            "/api/v1/courses/123/pages",
            data={"wiki_page": {"title": "Safe page", "body": "<p>Body</p>", "published": False}},
        )

    def test_api_path_rejects_external_and_query_urls(self):
        invalid = [
            "https://example.com/api/v1/courses",
            "/api/v1/courses?access_token=secret",
            "/api/v1//courses",
            "/api/v2/courses",
        ]
        for path in invalid:
            with self.subTest(path=path), self.assertRaises(ValueError):
                server.validate_api_path(path)

    def test_tool_annotations_mark_only_page_write_as_mutating(self):
        tools = {tool["name"]: tool for tool in server.TOOLS}
        self.assertTrue(tools["canvas_read_api"]["annotations"]["readOnlyHint"])
        for name in ("canvas_write_page", "canvas_create_module", "canvas_create_module_item", "canvas_create_assignment", "canvas_create_discussion", "canvas_create_classic_quiz", "canvas_create_classic_quiz_question"):
            self.assertFalse(tools[name]["annotations"]["readOnlyHint"])
            self.assertTrue(tools[name]["annotations"]["destructiveHint"])

    def test_module_write_uses_the_scoped_endpoint_and_confirmation(self):
        args = {"course_id": 123, "name": "Week 1", "confirmation": "APPROVE CANVAS MODULE WRITE course 123"}
        with mock.patch.object(server, "content_write", return_value={"id": 8}) as content_write:
            result = server.call_tool("canvas_create_module", args)
        self.assertEqual(result, {"id": 8})
        content_write.assert_called_once_with(123, args["confirmation"], "MODULE", "POST", "/api/v1/courses/123/modules", {"module": {"name": "Week 1", "published": False}})

    def test_module_item_rejects_missing_reference(self):
        with self.assertRaises(ValueError):
            server.call_tool("canvas_create_module_item", {"course_id": 123, "module_id": 8, "type": "Assignment", "title": "Task", "confirmation": "APPROVE CANVAS MODULE ITEM WRITE course 123"})

    def test_discussion_write_uses_canvas_top_level_payload(self):
        args = {"course_id": 123, "title": "Discussion", "message": "<p>Prompt</p>", "confirmation": "APPROVE CANVAS DISCUSSION WRITE course 123"}
        with mock.patch.object(server, "content_write", return_value={"id": 4}) as content_write:
            server.call_tool("canvas_create_discussion", args)
        content_write.assert_called_once_with(123, args["confirmation"], "DISCUSSION", "POST", "/api/v1/courses/123/discussion_topics", {"title": "Discussion", "message": "<p>Prompt</p>", "discussion_type": "threaded", "published": False})


if __name__ == "__main__":
    unittest.main()
