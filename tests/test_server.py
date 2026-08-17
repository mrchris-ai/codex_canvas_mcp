import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from canvas_mcp import server


def sample_rubric_args(assignment_url: str = "https://canvas.example.edu/courses/123/assignments/456"):
    return {
        "assignment_url": assignment_url,
        "title": "Sample Rubric",
        "criteria": [
            {
                "description": "Content",
                "long_description": "Explains the required content accurately and completely.",
                "points": 25,
                "ratings": [
                    {
                        "description": "Full Marks",
                        "long_description": "Fully meets this criterion with complete and accurate work.",
                        "points": 25,
                    },
                    {
                        "description": "No Marks",
                        "long_description": "Does not meet this criterion or provide relevant evidence.",
                        "points": 0,
                    },
                ],
            }
        ],
        "confirmation": "APPROVE CANVAS RUBRIC WRITE course 123",
    }


def write_sample_png(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"sample-image-data")


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

    def test_image_upload_requires_exact_confirmation_and_allowlist(self):
        policy = {"version": 1, "enabled": True, "approved_course_ids": [123]}
        with mock.patch.object(server, "write_policy", return_value=policy):
            with self.assertRaises(PermissionError):
                server.require_image_upload_approval(
                    456,
                    "APPROVE CANVAS IMAGE UPLOAD course 456",
                )
            with self.assertRaises(PermissionError):
                server.require_image_upload_approval(123, "yes")
            server.require_image_upload_approval(
                123,
                "APPROVE CANVAS IMAGE UPLOAD course 123",
            )

    def test_image_validation_is_rooted_typed_and_not_symlinked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "images"
            root.mkdir()
            image = root / "sample.png"
            write_sample_png(image)
            outside = Path(directory) / "outside.png"
            write_sample_png(outside)
            disguised = root / "disguised.png"
            disguised.write_bytes(b"not-a-png")
            symlink = root / "linked.png"
            symlink.symlink_to(image)
            environment = {"CANVAS_IMAGE_UPLOAD_ROOT": str(root)}
            with mock.patch.dict(os.environ, environment, clear=True):
                validated, content_type, size = server.validate_image_file(str(image))
                self.assertEqual(validated, image.resolve())
                self.assertEqual(content_type, "image/png")
                self.assertEqual(size, image.stat().st_size)
                with self.assertRaises(PermissionError):
                    server.validate_image_file(str(outside))
                with self.assertRaises(ValueError):
                    server.validate_image_file(str(disguised))
                with self.assertRaises(ValueError):
                    server.validate_image_file(str(symlink))

    def test_image_upload_root_is_required_and_absolute(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(server.ConfigurationError):
                server.image_upload_root()
        with mock.patch.dict(os.environ, {"CANVAS_IMAGE_UPLOAD_ROOT": "relative"}, clear=True):
            with self.assertRaises(server.ConfigurationError):
                server.image_upload_root()

    def test_multipart_upload_preserves_params_and_puts_file_last(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "sample.png"
            write_sample_png(image)
            with mock.patch.object(server.secrets, "token_hex", return_value="fixed"):
                body, boundary = server.multipart_image_body(
                    {"key": "opaque/key", "policy": "opaque-policy"},
                    image,
                    "image/png",
                )
        self.assertEqual(boundary, "----codex-canvas-fixed")
        self.assertLess(body.index(b'name="key"'), body.index(b'name="policy"'))
        self.assertLess(body.index(b'name="policy"'), body.index(b'name="file"'))
        self.assertIn(b"sample-image-data", body)

    def test_upload_url_rejects_local_and_non_https_hosts(self):
        for url in (
            "http://files.example.edu/upload",
            "https://localhost/upload",
            "https://127.0.0.1/upload",
            "https://user:password@files.example.edu/upload",
        ):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                server.validate_upload_url(url)
        self.assertEqual(
            server.validate_upload_url("https://files.example.edu/upload?signature=opaque"),
            "https://files.example.edu/upload?signature=opaque",
        )

    def test_image_upload_uses_three_step_flow_and_course_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "sample.png"
            write_sample_png(image)
            size = image.stat().st_size
            initial = {
                "upload_url": "https://files.example.edu/upload",
                "upload_params": {"key": "opaque/key", "policy": "opaque-policy"},
            }
            created = {"id": 99}
            verified = {
                "id": 99,
                "folder_id": 8,
                "display_name": "sample.png",
                "filename": "sample.png",
                "content-type": "image/png",
                "size": size,
            }
            args = {
                "course_id": 123,
                "file_path": str(image),
                "confirmation": "APPROVE CANVAS IMAGE UPLOAD course 123",
            }
            environment = {
                "CANVAS_BASE_URL": "https://canvas.example.edu",
                "CANVAS_IMAGE_UPLOAD_ROOT": str(root),
            }
            with (
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch.object(server, "require_image_upload_approval") as approval,
                mock.patch.object(server, "request_api", side_effect=[initial, created]) as request_api,
                mock.patch.object(
                    server,
                    "post_image_payload",
                    return_value=(302, "https://canvas.example.edu/api/v1/files/99/create_success?uuid=opaque"),
                ) as post_image_payload,
                mock.patch.object(server, "api_get", return_value=verified) as api_get,
            ):
                result = server.call_tool("canvas_upload_image", args)
        approval.assert_called_once_with(123, args["confirmation"])
        self.assertEqual(
            request_api.call_args_list,
            [
                mock.call(
                    "POST",
                    "/api/v1/courses/123/files",
                    data={
                        "name": "sample.png",
                        "size": size,
                        "content_type": "image/png",
                        "on_duplicate": "rename",
                    },
                ),
                mock.call(
                    "POST",
                    "/api/v1/files/99/create_success",
                    query={"uuid": ["opaque"]},
                ),
            ],
        )
        post_image_payload.assert_called_once_with(
            initial["upload_url"],
            initial["upload_params"],
            image.resolve(),
            "image/png",
        )
        api_get.assert_called_once_with("/api/v1/courses/123/files/99")
        self.assertEqual(result["canvas_path"], "/courses/123/files/99/preview")
        self.assertEqual(result["content_type"], "image/png")
        self.assertEqual(result["size"], size)

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
        for name in ("canvas_upload_image", "canvas_write_page", "canvas_create_module", "canvas_create_module_item", "canvas_create_assignment", "canvas_create_assignment_rubric", "canvas_create_discussion", "canvas_create_classic_quiz", "canvas_create_classic_quiz_question", "canvas_delete_page", "canvas_delete_assignment", "canvas_delete_discussion", "canvas_delete_classic_quiz", "canvas_delete_module"):
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

    def test_assignment_url_variants_are_resolved(self):
        variants = [
            ("https://canvas.example.edu/courses/123/assignments/456", (123, 456, None)),
            ("https://canvas.example.edu/courses/123/assignments/456/?module_item_id=999#rubric", (123, 456, None)),
            ("/courses/123/assignments/456/submissions/7", (123, 456, None)),
            ("https://canvas.example.edu/courses/123/modules/items/999", (123, None, 999)),
            ("https://canvas.example.edu/courses/123/gradebook/speed_grader?assignment_id=456", (123, 456, None)),
        ]
        with mock.patch.dict(os.environ, {"CANVAS_BASE_URL": "https://canvas.example.edu"}, clear=True):
            for assignment_url, expected in variants:
                with self.subTest(assignment_url=assignment_url):
                    self.assertEqual(server.parse_assignment_url(assignment_url), expected)
            with self.assertRaises(ValueError):
                server.parse_assignment_url("https://other.example.edu/courses/123/assignments/456")
            with self.assertRaises(ValueError):
                server.parse_assignment_url("https://canvas.example.edu/courses/123/assignments/456?assignment_id=457")

    def test_rubric_create_uses_url_course_and_scoped_endpoint(self):
        args = sample_rubric_args()
        assignment = {"id": 456, "points_possible": 25, "rubric": None, "rubric_settings": None}
        with (
            mock.patch.dict(os.environ, {"CANVAS_BASE_URL": "https://canvas.example.edu"}, clear=True),
            mock.patch.object(server, "require_write_approval") as require_write_approval,
            mock.patch.object(server, "api_get", return_value=assignment) as api_get,
            mock.patch.object(server, "request_api", return_value={"rubric": {"id": 9}}) as request_api,
        ):
            result = server.call_tool("canvas_create_assignment_rubric", args)
        self.assertEqual(result, {"rubric": {"id": 9}})
        require_write_approval.assert_called_once_with(123, args["confirmation"], "RUBRIC")
        api_get.assert_called_once_with(
            "/api/v1/courses/123/assignments/456",
            {"include[]": ["rubric", "rubric_settings"]},
        )
        request_api.assert_called_once()
        method, path = request_api.call_args.args
        payload = request_api.call_args.kwargs["data"]
        self.assertEqual((method, path), ("POST", "/api/v1/courses/123/rubrics"))
        self.assertEqual(payload["rubric_association"]["association_id"], 456)
        self.assertTrue(payload["rubric_association"]["use_for_grading"])
        self.assertEqual(payload["rubric"]["criteria"]["0"]["ratings"]["0"]["description"], "Full Marks")
        self.assertTrue(payload["rubric"]["criteria"]["0"]["ratings"]["0"]["long_description"])

    def test_rubric_module_item_url_resolves_assignment(self):
        args = sample_rubric_args("https://canvas.example.edu/courses/123/modules/items/999")
        responses = [
            {"id": 999, "type": "Assignment", "content_id": 456},
            {"id": 456, "points_possible": 25, "rubric": None, "rubric_settings": None},
        ]
        with (
            mock.patch.dict(os.environ, {"CANVAS_BASE_URL": "https://canvas.example.edu"}, clear=True),
            mock.patch.object(server, "require_write_approval"),
            mock.patch.object(server, "api_get", side_effect=responses) as api_get,
            mock.patch.object(server, "request_api", return_value={"ok": True}),
        ):
            server.call_tool("canvas_create_assignment_rubric", args)
        self.assertEqual(api_get.call_args_list[0], mock.call("/api/v1/courses/123/modules/items/999"))
        self.assertEqual(api_get.call_args_list[1], mock.call("/api/v1/courses/123/assignments/456", {"include[]": ["rubric", "rubric_settings"]}))

    def test_rubric_creation_refuses_existing_rubric_and_incomplete_ratings(self):
        args = sample_rubric_args()
        with (
            mock.patch.dict(os.environ, {"CANVAS_BASE_URL": "https://canvas.example.edu"}, clear=True),
            mock.patch.object(server, "require_write_approval"),
            mock.patch.object(server, "api_get", return_value={"id": 456, "points_possible": 25, "rubric_settings": {"id": 8}}),
            mock.patch.object(server, "request_api") as request_api,
        ):
            with self.assertRaises(ValueError):
                server.call_tool("canvas_create_assignment_rubric", args)
            request_api.assert_not_called()

        args["criteria"][0]["ratings"][0]["long_description"] = ""
        with self.assertRaises(ValueError):
            server.rubric_payload(args["criteria"])

    def test_grading_rubric_points_must_match_assignment(self):
        args = sample_rubric_args()
        args["criteria"][0]["points"] = 20
        args["criteria"][0]["ratings"][0]["points"] = 20
        with (
            mock.patch.dict(os.environ, {"CANVAS_BASE_URL": "https://canvas.example.edu"}, clear=True),
            mock.patch.object(server, "require_write_approval"),
            mock.patch.object(server, "api_get", return_value={"id": 456, "points_possible": 25, "rubric": None, "rubric_settings": None}),
            mock.patch.object(server, "request_api") as request_api,
        ):
            with self.assertRaises(ValueError):
                server.call_tool("canvas_create_assignment_rubric", args)
            request_api.assert_not_called()

        args = sample_rubric_args()
        args["criteria"][0]["points"] = float("nan")
        with self.assertRaises(ValueError):
            server.rubric_payload(args["criteria"])

    def test_delete_requires_exact_confirmation_and_allowlist(self):
        policy = {"version": 1, "enabled": True, "approved_course_ids": [123]}
        with mock.patch.object(server, "write_policy", return_value=policy):
            with self.assertRaises(PermissionError):
                server.require_delete_approval(456, "APPROVE CANVAS MODULE DELETE course 456", "MODULE")
            with self.assertRaises(PermissionError):
                server.require_delete_approval(123, "yes", "MODULE")
            server.require_delete_approval(123, "APPROVE CANVAS MODULE DELETE course 123", "MODULE")

    def test_delete_tools_use_only_scoped_endpoints(self):
        cases = [
            ("canvas_delete_page", {"course_id": 123, "page_url": "sample-page", "confirmation": "APPROVE CANVAS PAGE DELETE course 123"}, "PAGE", "/api/v1/courses/123/pages/sample-page"),
            ("canvas_delete_assignment", {"course_id": 123, "assignment_id": 8, "confirmation": "APPROVE CANVAS ASSIGNMENT DELETE course 123"}, "ASSIGNMENT", "/api/v1/courses/123/assignments/8"),
            ("canvas_delete_discussion", {"course_id": 123, "discussion_id": 9, "confirmation": "APPROVE CANVAS DISCUSSION DELETE course 123"}, "DISCUSSION", "/api/v1/courses/123/discussion_topics/9"),
            ("canvas_delete_classic_quiz", {"course_id": 123, "quiz_id": 10, "confirmation": "APPROVE CANVAS CLASSIC QUIZ DELETE course 123"}, "CLASSIC QUIZ", "/api/v1/courses/123/quizzes/10"),
            ("canvas_delete_module", {"course_id": 123, "module_id": 11, "confirmation": "APPROVE CANVAS MODULE DELETE course 123"}, "MODULE", "/api/v1/courses/123/modules/11"),
        ]
        for tool_name, args, resource, path in cases:
            with self.subTest(tool=tool_name), mock.patch.object(server, "content_delete", return_value={"ok": True}) as content_delete:
                self.assertEqual(server.call_tool(tool_name, args), {"ok": True})
                content_delete.assert_called_once_with(123, args["confirmation"], resource, path)


if __name__ == "__main__":
    unittest.main()
