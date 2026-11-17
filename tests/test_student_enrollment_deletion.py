import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from canvas_mcp import server


REAL_WRITE_POLICY = server.write_policy
PREVIEW = "canvas_preview_student_enrollment_deletion"
DELETE = "canvas_delete_student_enrollment"
GRANTS = "approved_student_enrollment_deletion_fingerprints"


class StudentEnrollmentDeletionTests(unittest.TestCase):
    def setUp(self):
        self.target = {
            "course_id": 30, "enrollment_id": 70, "canvas_user_id": 50,
            "section_id": 40, "sis_user_id": "0001234567",
            "sis_course_id": "SIS_30", "role_id": 60,
        }
        self.record = {
            "id": 70, "root_account_id": 10, "course_id": 30,
            "course_section_id": 40, "user_id": 50,
            "sis_user_id": "0001234567", "sis_course_id": "SIS_30",
            "role_id": 60, "role": "StudentEnrollment", "type": "StudentEnrollment",
            "enrollment_state": "active", "associated_user_id": None,
            "limit_privileges_to_course_section": False,
            "start_at": None, "end_at": None, "created_at": "2026-01-01T00:00:00Z",
            "sis_import_id": 80, "sis_section_id": "SECTION_40", "sis_account_id": "ACADEMIC",
            "course_integration_id": None, "section_integration_id": None,
        }
        self.records = [copy.deepcopy(self.record)]
        self.responses = {
            "/api/v1/courses/30": {
                "id": 30, "account_id": 20, "root_account_id": 10,
                "sis_course_id": "SIS_30", "name": "Test course", "workflow_state": "available",
            },
            "/api/v1/sections/40": {"id": 40, "course_id": 30, "sis_section_id": "SECTION_40"},
            "/api/v1/users/50/profile": {"id": 50, "sis_user_id": "0001234567", "name": "Test Student"},
            "/api/v1/users/sis_user_id:0001234567/profile": {"id": 50, "sis_user_id": "0001234567"},
        }
        self.roles = [{"id": 60, "role": "StudentEnrollment", "label": "Student",
                       "base_role_type": "StudentEnrollment", "workflow_state": "built_in"}]
        self.policy = {**server.disabled_policy(), "enabled": True}
        self.read = mock.patch.object(server, "api_get", side_effect=self.fake_read).start()
        self.list = mock.patch.object(server, "provisioning_list", side_effect=self.fake_list).start()
        self.write = mock.patch.object(server, "request_api", side_effect=self.fake_write).start()
        self.origin = mock.patch.object(server, "canvas_base_url", return_value="https://canvas.example.edu").start()
        self.token = mock.patch.object(server, "canvas_token", side_effect=AssertionError("No live credentials")).start()
        mock.patch.object(server, "write_policy", return_value=self.policy).start()
        self.addCleanup(mock.patch.stopall)

    def fake_read(self, path, query=None):
        self.assertIsNone(query)
        return copy.deepcopy(self.responses[path])

    def fake_list(self, path, query):
        if path == "/api/v1/accounts/20/roles":
            self.assertEqual(query, {"show_inherited": True})
            return copy.deepcopy(self.roles)
        self.assertEqual(path, "/api/v1/courses/30/enrollments")
        self.assertEqual(query["user_id"], 50)
        self.assertEqual(set(query["state[]"]), set(server.ENROLLMENT_STATES))
        return copy.deepcopy(self.records)

    def fake_write(self, method, path, **kwargs):
        self.assertEqual((method, path), ("DELETE", "/api/v1/courses/30/enrollments/70"))
        self.assertEqual(kwargs, {"data": {"task": "delete"}})  # No arbitrary body, role, section, or status mutation.
        self.records[0]["enrollment_state"] = "deleted"
        return copy.deepcopy(self.records[0])

    def preview(self, target=None):
        return server.call_tool(PREVIEW, self.target if target is None else target)

    def authorize(self):
        preview = self.preview()
        self.policy[GRANTS] = [preview["approval_fingerprint"]]
        return {**self.target, "confirmation": preview["confirmation"]}

    def test_preview_is_read_only_without_any_grant(self):
        self.policy["enabled"] = False
        result = self.preview()
        self.assertTrue(result["read_only"])
        self.assertTrue(result["can_delete"])
        self.assertEqual(result["target"], self.target)
        self.assertEqual(result["current_state"], "active")
        self.assertRegex(result["approval_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertIn(result["approval_fingerprint"], result["confirmation"])
        self.write.assert_not_called()
        self.token.assert_not_called()

    def test_exact_active_enrollment_uses_one_delete_and_independent_readback(self):
        args = self.authorize()
        self.read.reset_mock(); self.list.reset_mock()
        result = server.call_tool(DELETE, args)
        self.assertEqual((result["changed"], result["verified"], result["enrollment_state"]), (True, True, "deleted"))
        self.assertEqual(result["enrollment_id"], 70)
        self.assertEqual(result["target"], self.target)
        self.write.assert_called_once_with("DELETE", "/api/v1/courses/30/enrollments/70", data={"task": "delete"})
        enrollment_reads = [c for c in self.list.call_args_list if c.args[0].endswith("/enrollments")]
        self.assertEqual(len(enrollment_reads), 2)
        self.assertEqual(self.records[0], self.record | {"enrollment_state": "deleted"})

    def test_already_deleted_exact_enrollment_is_verified_noop(self):
        self.records[0]["enrollment_state"] = "deleted"
        args = self.authorize()
        result = server.call_tool(DELETE, args)
        self.assertFalse(result["changed"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["enrollment_state"], "deleted")
        self.write.assert_not_called()

    def test_invited_enrollment_can_be_deleted_with_same_exact_guards(self):
        self.records[0]["enrollment_state"] = "invited"
        args = self.authorize()
        result = server.call_tool(DELETE, args)
        self.assertEqual((result["changed"], result["verified"], result["enrollment_state"]), (True, True, "deleted"))
        self.write.assert_called_once_with("DELETE", "/api/v1/courses/30/enrollments/70", data={"task": "delete"})

    def test_other_states_are_not_deleted(self):
        args = self.authorize()
        for state in ("inactive", "creation_pending", "completed", "rejected", "unknown", None):
            with self.subTest(state=state):
                self.records[0]["enrollment_state"] = state
                with self.assertRaises(ValueError):
                    server.call_tool(DELETE, args)
        self.write.assert_not_called()

    def test_enrollment_identity_and_scope_mismatches_fail_closed(self):
        args = self.authorize()
        changes = [{"id": 71}, {"course_id": 31}, {"root_account_id": 11},
                   {"course_section_id": 41}, {"user_id": 51}, {"sis_user_id": "0001234568"},
                   {"sis_course_id": "OTHER"}, {"role_id": 61}, {"role": "TeacherEnrollment"},
                   {"type": "TeacherEnrollment"}]
        for change in changes:
            with self.subTest(change=change):
                self.records = [self.record | change]
                with self.assertRaises(ValueError):
                    server.call_tool(DELETE, args)
        self.write.assert_not_called()

    def test_missing_duplicate_or_additional_enrollments_fail_closed(self):
        args = self.authorize()
        for records in ([], [self.record, self.record], [self.record, self.record | {"id": 71}]):
            with self.subTest(records=records):
                self.records = copy.deepcopy(records)
                with self.assertRaises(ValueError):
                    server.call_tool(DELETE, args)
        self.write.assert_not_called()

    def test_course_section_and_bidirectional_sis_identity_are_verified(self):
        args = self.authorize()
        cases = [("/api/v1/courses/30", {"id": 31}),
                 ("/api/v1/courses/30", {"sis_course_id": "OTHER"}),
                 ("/api/v1/courses/30", {"workflow_state": "deleted"}),
                 ("/api/v1/courses/30", {"root_account_id": 11}),
                 ("/api/v1/sections/40", {"id": 41}),
                 ("/api/v1/sections/40", {"course_id": 31}),
                 ("/api/v1/users/50/profile", {"id": 51}),
                 ("/api/v1/users/50/profile", {"sis_user_id": "0001234568"}),
                 ("/api/v1/users/sis_user_id:0001234567/profile", {"id": 51}),
                 ("/api/v1/users/sis_user_id:0001234567/profile", {"sis_user_id": "0001234568"})]
        for path, change in cases:
            original = copy.deepcopy(self.responses[path])
            with self.subTest(path=path, change=change):
                self.responses[path].update(change)
                with self.assertRaises(ValueError):
                    server.call_tool(DELETE, args)
                self.responses[path] = original
        self.write.assert_not_called()

    def test_role_must_be_exact_active_builtin_student_role(self):
        args = self.authorize(); original = copy.deepcopy(self.roles[0])
        for change in ({"id": 61}, {"role": "TeacherEnrollment"}, {"base_role_type": "TeacherEnrollment"},
                       {"workflow_state": "inactive"}, {"label": "Custom Student"}):
            with self.subTest(change=change):
                self.roles = [original | change]
                with self.assertRaises(ValueError):
                    server.call_tool(DELETE, args)
        self.roles = []
        with self.assertRaises(ValueError): server.call_tool(DELETE, args)
        self.write.assert_not_called()

    def test_ids_and_unknown_fields_rejected_before_network(self):
        invalid = [{**self.target, "path": "/api/v1/courses/999"}, {**self.target, "task": "conclude"},
                   {**self.target, "sis_user_id": 1234567}, {**self.target, "sis_user_id": " "},
                   {**self.target, "sis_course_id": " "}, {**self.target, "sis_course_id": "SIS\n30"}]
        for key in ("course_id", "enrollment_id", "canvas_user_id", "section_id", "role_id"):
            invalid.extend({**self.target, key: bad} for bad in (True, False, 0, -1, "30", 1.5, None))
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError): self.preview(args)
        self.read.assert_not_called(); self.list.assert_not_called(); self.write.assert_not_called()

    def test_separate_grant_and_confirmation_required_before_network(self):
        args = self.authorize(); granted = copy.deepcopy(self.policy)
        for policy, confirmation in ((granted | {"enabled": False}, args["confirmation"]),
                                    (granted | {GRANTS: []}, args["confirmation"]),
                                    (granted, "yes"), (granted, args["confirmation"] + " extra")):
            self.read.reset_mock(); self.list.reset_mock()
            with self.subTest(policy=policy, confirmation=confirmation), \
                 mock.patch.object(server, "write_policy", return_value=policy), self.assertRaises(PermissionError):
                server.call_tool(DELETE, self.target | {"confirmation": confirmation})
            self.read.assert_not_called(); self.list.assert_not_called()
        self.write.assert_not_called()

    def test_broad_content_deactivation_and_other_admin_grants_do_not_authorize(self):
        args = self.authorize(); fingerprint = self.policy[GRANTS][0]
        self.policy.update({GRANTS: [], "approved_course_ids": [30],
                            "approved_student_deactivation_course_ids": [30],
                            "approved_instructor_enrollment_course_ids": [30],
                            "approved_instructor_role_ids": [60],
                            "approved_user_creation_fingerprints": [fingerprint],
                            "approved_sis_enrollment_import_fingerprints": [fingerprint],
                            "approved_student_reactivation_fingerprints": [fingerprint]})
        self.read.reset_mock(); self.list.reset_mock()
        with self.assertRaises(PermissionError): server.call_tool(DELETE, args)
        self.read.assert_not_called(); self.list.assert_not_called(); self.write.assert_not_called()

    def test_origin_and_every_target_field_are_bound_to_confirmation_and_grant(self):
        args = self.authorize()
        self.origin.return_value = "https://other.example.edu"
        with self.assertRaises(PermissionError): server.call_tool(DELETE, args)
        self.origin.return_value = "https://canvas.example.edu"
        for key, value in self.target.items():
            changed = value + 1 if isinstance(value, int) else value + "x"
            with self.subTest(key=key), self.assertRaises((ValueError, PermissionError)):
                server.call_tool(DELETE, args | {key: changed})
        self.write.assert_not_called()

    def test_delete_timeout_is_uncertain_and_never_retried(self):
        args = self.authorize()
        self.write.side_effect = TimeoutError("mock timeout")
        with self.assertRaisesRegex(RuntimeError, "[Ii]nspect|[Uu]ncertain|[Rr]etry"):
            server.call_tool(DELETE, args)
        self.write.assert_called_once()

    def test_readback_failure_does_not_claim_success_or_retry(self):
        args = self.authorize()
        def changed_then_unreadable(method, path, **kwargs):
            self.fake_write(method, path, **kwargs)
            self.read.side_effect = TimeoutError("mock readback timeout")
            return copy.deepcopy(self.records[0])
        self.write.side_effect = changed_then_unreadable
        with self.assertRaisesRegex(RuntimeError, "[Ii]nspect|[Uu]ncertain|[Rr]etry"):
            server.call_tool(DELETE, args)
        self.write.assert_called_once()

    def test_missing_readback_enrollment_is_uncertain_not_verified_deletion(self):
        args = self.authorize()
        def changed_then_missing(method, path, **kwargs):
            response = self.fake_write(method, path, **kwargs)
            self.records = []
            return response
        self.write.side_effect = changed_then_missing
        with self.assertRaisesRegex(RuntimeError, "[Ii]nspect|[Uu]ncertain|[Rr]etry"):
            server.call_tool(DELETE, args)
        self.write.assert_called_once()

    def test_readback_account_or_course_identity_drift_is_not_success(self):
        cases = [("/api/v1/users/50/profile", {"sis_user_id": "0001234568"}),
                 ("/api/v1/users/sis_user_id:0001234567/profile", {"id": 51}),
                 ("/api/v1/courses/30", {"sis_course_id": "OTHER"}),
                 ("/api/v1/sections/40", {"course_id": 31})]
        for path, change in cases:
            with self.subTest(path=path, change=change):
                self.records = [copy.deepcopy(self.record)]
                original = copy.deepcopy(self.responses[path])
                args = self.authorize(); self.write.reset_mock()
                def changed_then_drift(method, endpoint, **kwargs):
                    response = self.fake_write(method, endpoint, **kwargs)
                    self.responses[path].update(change)
                    return response
                self.write.side_effect = changed_then_drift
                with self.assertRaises(RuntimeError): server.call_tool(DELETE, args)
                self.write.assert_called_once()
                self.responses[path] = original

    def test_unverified_response_or_protected_field_drift_never_claims_success(self):
        changes = [{"enrollment_state": "active"}, {"enrollment_state": "invited"},
                   {"sis_import_id": 81}, {"limit_privileges_to_course_section": True},
                   {"associated_user_id": 51}, {"start_at": "2026-02-01T00:00:00Z"},
                   {"end_at": "2026-03-01T00:00:00Z"}, {"created_at": "2026-02-01T00:00:00Z"},
                   {"sis_section_id": "OTHER"}, {"sis_account_id": "OTHER"},
                   {"course_integration_id": "OTHER"}, {"section_integration_id": "OTHER"},
                   {"course_section_id": 41}, {"sis_user_id": "0001234568"}, {"role_id": 61}]
        for change in changes:
            with self.subTest(change=change):
                self.records = [copy.deepcopy(self.record)]
                args = self.authorize(); self.write.reset_mock()
                def changed(method, path, **kwargs):
                    response = self.fake_write(method, path, **kwargs)
                    self.records[0].update(change)
                    return response  # A successful write response alone is insufficient.
                self.write.side_effect = changed
                with self.assertRaises(RuntimeError): server.call_tool(DELETE, args)
                self.write.assert_called_once()

    def test_tool_contract_is_narrow_and_mutation_is_not_a_read(self):
        tools = {t["name"]: t for t in server.TOOLS}
        for name, mutating in ((PREVIEW, False), (DELETE, True)):
            tool = tools[name]
            self.assertEqual(tool["annotations"]["readOnlyHint"], not mutating)
            self.assertEqual(tool["annotations"]["destructiveHint"], mutating)
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
            expected = set(self.target) | ({"confirmation"} if mutating else set())
            self.assertEqual(set(tool["inputSchema"]["properties"]), expected)
            self.assertEqual(set(tool["inputSchema"]["required"]), expected)


class StudentEnrollmentDeletionPolicyTests(unittest.TestCase):
    def test_version_12_requires_unique_exact_fingerprints(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"; path.touch(mode=0o600)
            policy = server.disabled_policy() | {"enabled": True, GRANTS: ["a" * 64]}
            self.assertEqual(policy["version"], 12)
            with mock.patch.dict(os.environ, {"CANVAS_WRITE_POLICY": str(path)}):
                path.write_text(json.dumps(policy)); self.assertEqual(REAL_WRITE_POLICY(), policy)
                for invalid in ([True], ["*"], ["a" * 63], ["A" * 64], ["a" * 64] * 2, "a" * 64, None):
                    path.write_text(json.dumps(policy | {GRANTS: invalid}))
                    with self.subTest(invalid=invalid), self.assertRaises(server.ConfigurationError): REAL_WRITE_POLICY()

    def test_all_legacy_versions_preserve_old_grants_and_gain_no_deletion_grant(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"; path.touch(mode=0o600)
            with mock.patch.dict(os.environ, {"CANVAS_WRITE_POLICY": str(path)}):
                for version in range(1, 12):
                    policy = {"version": version, "enabled": True, "approved_course_ids": [30]}
                    if version >= 2: policy["approved_rubric_delete_course_ids"] = [31]
                    if version >= 3: policy["approved_student_deactivation_course_ids"] = [32]
                    if version >= 4:
                        policy.update(approved_course_creation_account_ids=[], approved_instructor_enrollment_course_ids=[], approved_instructor_role_ids=[])
                    if version >= 5: policy.update(approved_course_creation_targets=[], approved_instructor_enrollment_targets=[])
                    if version >= 6: policy["approved_user_update_targets"] = []
                    if version == 7: policy["approved_student_enrollment_targets"] = []
                    if version >= 9: policy["approved_user_creation_fingerprints"] = ["b" * 64]
                    if version >= 10: policy["approved_sis_enrollment_import_fingerprints"] = ["c" * 64]
                    if version >= 11: policy["approved_student_reactivation_fingerprints"] = ["d" * 64]
                    with self.subTest(version=version):
                        path.write_text(json.dumps(policy)); result = REAL_WRITE_POLICY()
                        self.assertEqual(result["version"], 12)
                        self.assertEqual(result[GRANTS], [])
                        for key, value in policy.items():
                            if key not in ("version", "approved_student_enrollment_targets"):
                                self.assertEqual(result[key], value)
                        path.write_text(json.dumps(policy | {GRANTS: ["a" * 64]}))
                        with self.assertRaises(server.ConfigurationError): REAL_WRITE_POLICY()


if __name__ == "__main__":
    unittest.main()
