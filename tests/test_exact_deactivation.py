import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from canvas_mcp import server


KEY = "approved_student_deactivation_grants"


class ExactDeactivationTests(unittest.TestCase):
    def setUp(self):
        self.target = {"course_id": 10, "section_id": 20, "enrollment_id": 30,
                       "canvas_user_id": 40, "sis_user_id": "0000000040"}
        self.record = {"id": 30, "course_id": 10, "course_section_id": 20,
                       "user_id": 40, "sis_user_id": "0000000040",
                       "role": "StudentEnrollment", "type": "StudentEnrollment",
                       "role_id": 50, "root_account_id": 60, "enrollment_state": "active"}
        origin = mock.patch.object(server, "canvas_base_url", return_value="https://canvas.example.edu")
        origin.start(); self.addCleanup(origin.stop)
        self.digest = server.student_deactivation_fingerprint(self.target)
        self.confirmation = "APPROVE CANVAS STUDENT DEACTIVATION " + self.digest
        self.policy = server.disabled_policy() | {"enabled": True,
            KEY: [{"fingerprint": self.digest, "expires_at": self.expiry(300)}]}

    def expiry(self, seconds):
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def run_action(self, target=None, confirmation=None):
        return server.deactivate_student_enrollment({**(target or self.target),
            "confirmation": confirmation or self.confirmation})

    def test_exact_grant_permits_only_deactivate_and_verifies(self):
        after = self.record | {"enrollment_state": "inactive"}
        with mock.patch.object(server, "write_policy", return_value=self.policy), mock.patch.object(
                server, "api_get", side_effect=[[self.record], [after]]), mock.patch.object(
                server, "request_api") as write:
            result = self.run_action()
        self.assertEqual(self.policy["approved_student_deactivation_course_ids"], [])
        self.assertTrue(result["changed"] and result["verified"])
        write.assert_called_once_with("DELETE", "/api/v1/courses/10/enrollments/30", query={"task": "deactivate"})

    def test_missing_disabled_expired_distant_or_wrong_confirmation_blocks_network(self):
        cases = [(self.policy | {"enabled": False}, self.confirmation),
                 (self.policy | {KEY: []}, self.confirmation),
                 (self.policy | {KEY: [{"fingerprint": self.digest, "expires_at": self.expiry(-1)}]}, self.confirmation),
                 (self.policy | {KEY: [{"fingerprint": self.digest, "expires_at": self.expiry(1000)}]}, self.confirmation),
                 (self.policy, "APPROVE CANVAS STUDENT DEACTIVATION course 10 enrollment 30"),
                 (self.policy, "yes")]
        for policy, confirmation in cases:
            with self.subTest(policy=policy, confirmation=confirmation), mock.patch.object(
                    server, "write_policy", return_value=policy), mock.patch.object(server, "api_get") as read, mock.patch.object(server, "request_api") as write:
                with self.assertRaises(PermissionError): self.run_action(confirmation=confirmation)
                read.assert_not_called(); write.assert_not_called()

    def test_each_identity_field_and_origin_is_bound(self):
        for key, value in self.target.items():
            changed = self.target | {key: value + 1 if isinstance(value, int) else "0000000041"}
            with self.subTest(key=key), mock.patch.object(server, "write_policy", return_value=self.policy), mock.patch.object(server, "api_get") as read:
                with self.assertRaises(PermissionError): self.run_action(target=changed)
                read.assert_not_called()
        with mock.patch.object(server, "canvas_base_url", return_value="https://other.example.edu"), mock.patch.object(server, "write_policy", return_value=self.policy), mock.patch.object(server, "api_get") as read:
            with self.assertRaises(PermissionError): self.run_action()
            read.assert_not_called()

    def test_already_inactive_noop_and_uncertain_write_never_retried(self):
        with mock.patch.object(server, "write_policy", return_value=self.policy), mock.patch.object(server, "api_get", return_value=[self.record | {"enrollment_state": "inactive"}]), mock.patch.object(server, "request_api") as write:
            self.assertFalse(self.run_action()["changed"]); write.assert_not_called()
        with mock.patch.object(server, "write_policy", return_value=self.policy), mock.patch.object(server, "api_get", return_value=[self.record]), mock.patch.object(server, "request_api", side_effect=RuntimeError("uncertain")) as write:
            with self.assertRaises(RuntimeError): self.run_action()
            self.assertEqual(write.call_count, 1)

    def test_protected_role_and_section_drift_fails_readback(self):
        for change in ({"role_id": 51}, {"course_section_id": 21}, {"root_account_id": 61}):
            after = self.record | {"enrollment_state": "inactive"} | change
            with self.subTest(change=change), mock.patch.object(server, "write_policy", return_value=self.policy), mock.patch.object(server, "api_get", side_effect=[[self.record], [after]]), mock.patch.object(server, "request_api") as write:
                with self.assertRaises(RuntimeError): self.run_action()
                self.assertEqual(write.call_count, 1)

    def test_permission_revoked_during_preflight_blocks_write(self):
        revoked = self.policy | {KEY: []}
        with mock.patch.object(server, "write_policy", side_effect=[self.policy, revoked]), mock.patch.object(server, "api_get", return_value=[self.record]), mock.patch.object(server, "request_api") as write:
            with self.assertRaises(PermissionError): self.run_action()
            write.assert_not_called()

    def test_content_reactivation_and_deletion_grants_do_not_authorize_deactivation(self):
        unrelated = self.policy | {KEY: [], "approved_course_ids": [10],
            "approved_student_reactivation_fingerprints": [self.digest],
            "approved_student_enrollment_deletion_fingerprints": [self.digest]}
        with mock.patch.object(server, "write_policy", return_value=unrelated), mock.patch.object(server, "api_get") as read, mock.patch.object(server, "request_api") as write:
            with self.assertRaises(PermissionError): self.run_action()
            read.assert_not_called(); write.assert_not_called()

    def test_preview_has_bound_confirmation_without_policy_or_writes(self):
        with mock.patch.object(server, "api_get", return_value=[self.record]), mock.patch.object(server, "write_policy") as policy, mock.patch.object(server, "request_api") as write:
            preview = server.preview_student_deactivation(self.target)
        self.assertEqual(preview["approval_fingerprint"], self.digest)
        self.assertEqual(preview["confirmation"], self.confirmation)
        policy.assert_not_called(); write.assert_not_called()

    def test_strict_policy_and_version12_migration_preserve_other_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"; path.touch(mode=0o600)
            with mock.patch.dict(os.environ, {"CANVAS_WRITE_POLICY": str(path)}):
                old = {k: v for k, v in self.policy.items() if k != KEY} | {"version": 12}
                path.write_text(json.dumps(old)); parsed = server.write_policy()
                self.assertEqual(parsed[KEY], [])
                for key, value in old.items():
                    if key != "version": self.assertEqual(parsed[key], value)
                path.write_text(json.dumps(self.policy)); self.assertEqual(server.write_policy(), self.policy)
                grant = self.policy[KEY][0]
                for bad in (None, "*", [grant, grant], [grant | {"extra": True}],
                            [grant | {"expires_at": "2099-02-30T00:00:00Z"}],
                            [grant | {"expires_at": "2099-01-01"}],
                            [grant | {"fingerprint": "*"}]):
                    path.write_text(json.dumps(self.policy | {KEY: bad}))
                    with self.subTest(bad=bad), self.assertRaises(server.ConfigurationError): server.write_policy()
                path.write_text(json.dumps(old | {KEY: self.policy[KEY]}))
                with self.assertRaises(server.ConfigurationError): server.write_policy()


if __name__ == "__main__":
    unittest.main()
