import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError

from canvas_mcp import server


class UserCreationTests(unittest.TestCase):
    def setUp(self):
        self.target = {"account_id": 10, "name": "Sample Student", "short_name": "Sample Student",
                       "sortable_name": "Student, Sample", "email": "sample@school.example",
                       "sis_user_id": "0012345678"}
        self.account = {"id": 10, "name": "Example School", "parent_account_id": None, "workflow_state": "active"}
        self.permissions = {"manage_user_logins": True, "manage_sis": True}
        self.profile = {"id": 101, **{key: self.target[key] for key in ("name", "short_name", "sortable_name")}}
        self.login = {"id": 102, "account_id": 10, "user_id": 101, "workflow_state": "active",
                      "unique_id": self.target["email"], "sis_user_id": self.target["sis_user_id"]}
        self.channel = {"id": 103, "type": "email", "address": self.target["email"]}
        self.matches = {}
        self.results = {"/api/v1/accounts/10/users": [], "/api/v1/users/101/logins": [self.login],
                        "/api/v1/users/101/communication_channels": [self.channel]}
        self.enterContext(mock.patch.object(server, "canvas_base_url", return_value="https://school.example"))
        self.read = self.enterContext(mock.patch.object(server, "api_get", side_effect=self.get))
        self.enterContext(mock.patch.object(server, "provisioning_list", side_effect=lambda path, query: self.results[path]))
        self.post = self.enterContext(mock.patch.object(server, "request_api", return_value={"id": 101}))
        fingerprint = server.user_creation_fingerprint(self.target)
        self.policy = self.enterContext(mock.patch.object(server, "write_policy", return_value={
            "enabled": True, "approved_user_creation_fingerprints": [fingerprint],
        }))
        self.apply_args = {**self.target, "confirmation": f"APPROVE CANVAS USER CREATION {fingerprint}"}

    def get(self, path, query=None):
        if path == "/api/v1/accounts/self":
            return self.account
        if path == "/api/v1/accounts/10/permissions":
            return self.permissions
        if path == "/api/v1/users/101/profile":
            return self.profile
        if path in {"/api/v1/users/sis_user_id:0012345678/profile",
                    "/api/v1/users/sis_login_id:sample%40school.example/profile"}:
            if path in self.matches:
                return self.matches[path]
            raise RuntimeError("lookup failed") from HTTPError(path, 404, "Not Found", {}, None)
        raise AssertionError(f"Unexpected read {path}")

    def test_preview_and_verified_write(self):
        preview = server.call_tool("canvas_preview_user_creation", self.target)
        self.assertEqual(preview["confirmation"], self.apply_args["confirmation"])
        self.post.assert_not_called()
        result = server.call_tool("canvas_create_user_account", self.apply_args)
        self.assertTrue(result["verified"])
        self.assertEqual(result["canvas_user_id"], 101)
        self.post.assert_called_once_with("POST", "/api/v1/accounts/10/users", data={
            "user": {"name": "Sample Student", "short_name": "Sample Student", "sortable_name": "Student, Sample"},
            "pseudonym": {"unique_id": "sample@school.example", "sis_user_id": "0012345678", "send_confirmation": True},
            "communication_channel": {"type": "email", "address": "sample@school.example"},
        })

    def test_rejects_unapproved_targets_and_extra_fields(self):
        for change in ({"name": "Different Student"}, {"confirmation": "wrong"}):
            with self.subTest(change=change), self.assertRaises(PermissionError):
                server.create_user_account({**self.apply_args, **change})
        with self.assertRaises(ValueError):
            server.create_user_account({**self.apply_args, "password": "unallowed"})
        self.policy.return_value["enabled"] = False
        with self.assertRaises(PermissionError):
            server.create_user_account(self.apply_args)
        self.read.assert_not_called()
        self.post.assert_not_called()

    def test_rechecks_permissions_and_duplicates_at_write_time(self):
        server.preview_user_creation(self.target)
        for path in ("/api/v1/users/sis_user_id:0012345678/profile",
                     "/api/v1/users/sis_login_id:sample%40school.example/profile"):
            self.matches[path] = {"id": 104}
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "existing Canvas account"):
                server.create_user_account(self.apply_args)
            del self.matches[path]
        self.results["/api/v1/accounts/10/users"] = [{"id": 105}]
        with self.assertRaisesRegex(ValueError, "requested email"):
            server.create_user_account(self.apply_args)
        self.permissions["manage_sis"] = False
        with self.assertRaises(PermissionError):
            server.create_user_account(self.apply_args)
        self.post.assert_not_called()

    def test_unexpected_lookup_error_stops_before_post(self):
        self.read.side_effect = [self.account, self.permissions, RuntimeError("Unavailable")]
        with self.assertRaisesRegex(RuntimeError, "Unavailable"):
            server.create_user_account(self.apply_args)
        self.post.assert_not_called()

    def test_rejects_other_root_account_before_post(self):
        self.account["id"] = 11
        with self.assertRaisesRegex(ValueError, "exact active Canvas root account"):
            server.create_user_account(self.apply_args)
        self.post.assert_not_called()

    def test_read_back_mismatch_reports_user_id_without_retry(self):
        self.login["sis_user_id"] = "9999999999"
        with self.assertRaisesRegex(RuntimeError, "user ID: 101"):
            server.create_user_account(self.apply_args)
        self.post.assert_called_once()

    def test_unknown_write_outcome_does_not_retry(self):
        self.post.side_effect = RuntimeError("Connection failed")
        with self.assertRaisesRegex(RuntimeError, "Do not automatically retry"):
            server.create_user_account(self.apply_args)
        self.post.assert_called_once()


class UserCreationPolicyTests(unittest.TestCase):
    def test_approval_list_validates_and_defaults_to_empty_for_legacy_policy(self):
        policy = server.disabled_policy()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.touch(mode=0o600)
            with mock.patch.dict(os.environ, {"CANVAS_WRITE_POLICY": str(path)}):
                for values in ([], ["a" * 64]):
                    current = {**policy, "approved_user_creation_fingerprints": values}
                    path.write_text(json.dumps(current))
                    self.assertEqual(server.write_policy(), current)
                for bad in ("a" * 64, [123], ["short"], ["a" * 64, "a" * 64]):
                    path.write_text(json.dumps({**policy, "approved_user_creation_fingerprints": bad}))
                    with self.subTest(bad=bad), self.assertRaises(server.ConfigurationError):
                        server.write_policy()
                policy.pop("approved_user_creation_fingerprints")
                policy.pop("approved_sis_enrollment_import_fingerprints")
                policy.pop("approved_student_reactivation_fingerprints")
                policy.pop("approved_student_enrollment_deletion_fingerprints")
                path.write_text(json.dumps({**policy, "version": 8}))
                self.assertEqual(server.write_policy()["approved_user_creation_fingerprints"], [])
