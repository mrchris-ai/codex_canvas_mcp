"""Provisioning safety tests. All Canvas/network/credential boundaries are mocked."""
import copy
import io
from email.message import Message
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from canvas_mcp import server


class ProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.course_target = dict(account_id=10, enrollment_term_id=20, name="DS Sample", course_code="DS_SAMPLE")
        self.instructor_target = dict(course_id=30, section_id=40, canvas_user_id=50,
                                      sis_user_id="0000000050", role_id=60, role_name="DSTeacher")
        self.account = dict(id=10, root_account_id=1, parent_account_id=1, workflow_state="active", name="Development")
        self.term = dict(id=20, workflow_state="active", name="Default Term")
        self.course = dict(id=30, **self.course_target, workflow_state="unpublished",
                           is_public=False, is_public_to_auth_users=False, public_syllabus=False,
                           public_syllabus_to_auth=False, open_enrollment=False, self_enrollment=False)
        self.section = dict(id=40, course_id=30)
        self.user = dict(id=50, sis_user_id="0000000050", name="Sample Instructor")
        self.role = dict(id=60, label="DSTeacher", base_role_type="TeacherEnrollment", workflow_state="active")
        self.enrollment = dict(id=70, course_id=30, course_section_id=40, user_id=50,
                               sis_user_id="0000000050", role_id=60, role="DSTeacher",
                               type="TeacherEnrollment", enrollment_state="invited",
                               limit_privileges_to_course_section=False)
        self.records = []
        self.created = False
        self.policy = {**server.disabled_policy(), "enabled": True,
                       "approved_course_creation_targets": [self.course_target],
                       "approved_instructor_enrollment_targets": [self.instructor_target],
                       "approved_course_creation_account_ids": [],
                       "approved_instructor_enrollment_course_ids": [], "approved_instructor_role_ids": [60]}
        self.read = mock.patch.object(server, "api_get", side_effect=self.fake_read).start()
        self.pages = mock.patch.object(server, "api_get_page",
            side_effect=lambda path, query: (self.read(path, {**query, "page": 1}), "")).start()
        self.write = mock.patch.object(server, "request_api", side_effect=self.fake_write).start()
        mock.patch.object(server, "write_policy", return_value=self.policy).start()
        mock.patch.object(server, "canvas_base_url", return_value="https://canvas.example.edu").start()
        # Any accidental credential call fails rather than reading a developer's credentials.
        mock.patch.object(server, "canvas_token", side_effect=AssertionError("No credential access")).start()
        self.addCleanup(mock.patch.stopall)

    def fake_read(self, path, query=None):
        mapping = {"/api/v1/accounts/10": self.account, "/api/v1/accounts/1/terms/20": self.term,
                   "/api/v1/courses/30": self.course, "/api/v1/sections/40": self.section,
                   "/api/v1/users/50/profile": self.user}
        if path in mapping:
            return copy.deepcopy(mapping[path])
        if path == "/api/v1/accounts/10/courses":
            return []
        if path == "/api/v1/accounts/10/roles":
            return [copy.deepcopy(self.role)] if query["page"] == 1 else []
        if path == "/api/v1/courses/30/enrollments":
            return copy.deepcopy(([self.enrollment] if self.created else self.records) if query["page"] == 1 else [])
        raise AssertionError(f"Unexpected read: {path}")

    def fake_write(self, method, path, **kwargs):
        if method == "POST" and path == "/api/v1/accounts/10/courses":
            return {"id": 30}
        if method == "POST" and path == "/api/v1/courses/30/enrollments":
            self.created = True
            return {"id": 70}
        raise AssertionError("Unexpected mutation")

    def apply(self, kind, **overrides):
        target = self.course_target if kind == "course" else self.instructor_target
        args = {**target, "confirmation": server.provisioning_confirmation(kind, target), **overrides}
        return (server.create_course_shell if kind == "course" else server.enroll_instructor)(args)

    def test_previews_are_read_only_and_bind_every_requested_field(self):
        for kind, name, target in (("course", "canvas_preview_course_creation", self.course_target),
                                   ("instructor", "canvas_preview_instructor_enrollment", self.instructor_target)):
            result = server.call_tool(name, target)
            self.assertEqual(result["confirmation"], server.provisioning_confirmation(kind, target))
            for key, value in target.items():
                changed = {**target, key: value + 1 if isinstance(value, int) else value + "x"}
                self.assertNotEqual(result["confirmation"], server.provisioning_confirmation(kind, changed))
        self.write.assert_not_called()

    def test_course_payload_and_readback(self):
        result = self.apply("course")
        self.assertTrue(result["verified"])
        self.assertEqual(result["course_url"], "https://canvas.example.edu/courses/30")
        method, path = self.write.call_args.args
        self.assertEqual((method, path), ("POST", "/api/v1/accounts/10/courses"))
        payload = self.write.call_args.kwargs["data"]
        self.assertEqual(payload, {"course": {"name": "DS Sample", "course_code": "DS_SAMPLE", "term_id": 20,
            "is_public": False, "is_public_to_auth_users": False, "public_syllabus": False,
            "public_syllabus_to_auth": False, "open_enrollment": False, "self_enrollment": False},
            "offer": False, "enroll_me": False, "skip_course_template": True})
        self.read.assert_any_call("/api/v1/courses/30")

    def test_instructor_payload_is_invited_teacher_only_and_verified(self):
        result = self.apply("instructor")
        self.assertTrue(result["verified"])
        self.assertEqual(result["enrollment_state"], "invited")
        self.write.assert_called_once_with("POST", "/api/v1/courses/30/enrollments", data={"enrollment": {
            "user_id": 50, "type": "TeacherEnrollment", "role_id": 60, "course_section_id": 40,
            "enrollment_state": "invited", "limit_privileges_to_course_section": False, "notify": True}})

    def default_section_reads(self, path, query=None):
        if path == "/api/v1/courses/30/sections":
            return [self.section] if self.created and query["page"] == 1 else []
        return self.fake_read(path, query)

    def test_empty_shell_default_section_invitation_and_readback(self):
        self.instructor_target["section_id"] = 0
        self.read.side_effect = self.default_section_reads
        preview = server.preview_instructor_enrollment(self.instructor_target)
        self.assertIn("default section", preview["effect"])
        self.write.assert_not_called()
        result = self.apply("instructor")
        self.assertEqual(result["section_id"], 40)
        self.assertTrue(result["verified"])
        self.assertNotIn("course_section_id", self.write.call_args.kwargs["data"]["enrollment"])
        # Once Canvas creates a section, zero cannot authorize another invitation.
        self.write.reset_mock()
        with self.assertRaises(ValueError): self.apply("instructor")
        self.write.assert_not_called()

    def test_default_section_rejects_existing_sections_enrollments_and_unsafe_courses(self):
        self.instructor_target["section_id"] = 0
        self.read.side_effect = self.default_section_reads
        for key, value in (("workflow_state", "available"), ("is_public", True),
                           ("sis_course_id", "official"), ("blueprint", True), ("template", True)):
            old = self.course.get(key)
            self.course[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.apply("instructor")
            self.course[key] = old
        self.records = [self.enrollment]
        with self.assertRaises(ValueError): self.apply("instructor")
        self.write.assert_not_called()

    def test_default_section_bad_readback_is_uncertain_without_retry(self):
        self.instructor_target["section_id"] = 0
        for bad in ([], [dict(id=40, course_id=31)], [self.section, dict(id=41, course_id=30)],
                    [{**self.section, "sis_section_id": "official"}]):
            self.created = False
            self.write.reset_mock()
            def read(path, query=None):
                if path == "/api/v1/courses/30/sections":
                    return bad if self.created and query["page"] == 1 else []
                return self.fake_read(path, query)
            self.read.side_effect = read
            with self.subTest(bad=bad), self.assertRaisesRegex(RuntimeError, "Do not automatically retry"):
                self.apply("instructor")
            self.assertEqual(self.write.call_count, 1)

    def test_missing_separate_policy_and_confirmation_block_all_network(self):
        for kind, key in (("course", "approved_course_creation_targets"),
                          ("instructor", "approved_instructor_enrollment_targets"),
                          ("instructor", "approved_instructor_role_ids")):
            with self.subTest(kind=kind, key=key):
                saved = self.policy[key]
                self.policy[key] = []
                self.policy["approved_course_ids"] = [10, 30]
                self.policy["approved_student_deactivation_course_ids"] = [30]
                with self.assertRaises(PermissionError): self.apply(kind)
                self.policy[key] = saved
        for kind in ("course", "instructor"):
            with self.assertRaises(PermissionError): self.apply(kind, confirmation="yes")
            self.policy["enabled"] = False
            with self.assertRaises(PermissionError): self.apply(kind)
            self.policy["enabled"] = True
        self.read.assert_not_called()
        self.write.assert_not_called()

    def test_exact_approval_rejects_every_changed_field_before_network(self):
        for kind, target in (("course", self.course_target), ("instructor", self.instructor_target)):
            for key, value in target.items():
                changed = {**target, key: value + 1 if type(value) is int else value + "x"}
                confirmation = server.provisioning_confirmation(kind, changed)
                with self.subTest(kind=kind, key=key), self.assertRaises(PermissionError):
                    self.apply(kind, **changed, confirmation=confirmation)
        self.read.assert_not_called()
        self.write.assert_not_called()

    def test_legacy_broad_lists_never_authorize_provisioning(self):
        self.policy.update(approved_course_creation_targets=[], approved_instructor_enrollment_targets=[],
                           approved_course_creation_account_ids=[10], approved_instructor_enrollment_course_ids=[30])
        for kind in ("course", "instructor"):
            with self.assertRaises(PermissionError): self.apply(kind)
        self.read.assert_not_called()
        self.write.assert_not_called()

    def test_reject_unknown_args_bool_ids_and_unsafe_strings(self):
        for kind, target in (("course", self.course_target), ("instructor", self.instructor_target)):
            for extra in ({"path": "/api/v1/courses"}, {"enrollment_state": "active"}, {"offer": True}):
                with self.assertRaises(ValueError): self.apply(kind, **extra)
            for key, value in target.items():
                with self.subTest(key=key), self.assertRaises(ValueError):
                    self.apply(kind, **{key: True if isinstance(value, int) else "\ninvalid"})
        self.write.assert_not_called()
        self.read.assert_not_called()

    def test_root_inactive_account_wrong_term_and_duplicate_block_create(self):
        for field, value in (("root_account_id", 10), ("parent_account_id", None), ("workflow_state", "deleted"), ("id", 11)):
            original = self.account[field]
            self.account[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.apply("course")
            self.account[field] = original
        self.term["id"] = 21
        with self.assertRaises(ValueError): self.apply("course")
        self.term["id"] = 20
        original_read = self.fake_read
        def duplicates(path, query=None):
            if path.endswith("/courses"):
                return [self.course] if query["page"] == 1 else []
            return original_read(path, query)
        self.read.side_effect = duplicates
        with self.assertRaisesRegex(ValueError, "already exists"): self.apply("course")
        self.write.assert_not_called()

    def test_identity_section_role_and_course_mismatches_block_enrollment(self):
        for record, field, bad in ((self.user,"id",51),(self.user,"sis_user_id","other"),
            (self.section,"course_id",31),(self.role,"label","Teacher"),(self.role,"base_role_type","AccountMembership"),
            (self.role,"workflow_state","inactive"),(self.course,"workflow_state","completed")):
            original=record[field]; record[field]=bad
            with self.subTest(field=field,bad=bad), self.assertRaises(ValueError): self.apply("instructor")
            record[field]=original
        self.write.assert_not_called()

    def test_existing_exact_active_or_invited_is_noop(self):
        for state in ("active", "invited"):
            self.records = [{**self.enrollment, "enrollment_state": state}]
            result = self.apply("instructor")
            self.assertFalse(result["changed"])
            self.assertEqual(result["enrollment_state"], state)
        self.write.assert_not_called()

    def test_existing_conflicts_never_reactivate_or_change_role(self):
        for changes in ({"enrollment_state":"inactive"},{"enrollment_state":"deleted"},
            {"enrollment_state":"completed"},{"enrollment_state":"rejected"},{"enrollment_state":"creation_pending"},
            {"type":"StudentEnrollment"},{"role_id":61},{"course_section_id":41},
            {"limit_privileges_to_course_section":True}):
            self.records = [{**self.enrollment, **changes}]
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.apply("instructor")
        self.records = [self.enrollment, {**self.enrollment,"id":71}]
        with self.assertRaises(ValueError): self.apply("instructor")
        self.write.assert_not_called()

    def test_canvas_next_links_are_followed_without_inventing_page_two(self):
        link = '<https://canvas.example.edu/api/v1/example?page=opaque%3Acursor&per_page=100>; rel="next"'
        self.pages.side_effect = [([{"id":1}], link), ([{"id":2}], "")]
        self.assertEqual(len(server.provisioning_list("/api/v1/example", {})), 2)
        self.assertEqual(self.pages.call_args_list[1].args, ("/api/v1/example", "page=opaque%3Acursor&per_page=100"))
        self.pages.reset_mock()
        self.pages.side_effect = [([{"id":1}], "")]
        self.assertEqual(len(server.provisioning_list("/api/v1/example", {})), 1)
        self.pages.assert_called_once()

    def test_bad_pagination_fails_closed(self):
        for bad in ({}, [{"id":True}], [{"id":0}], [None]):
            self.pages.side_effect = [(bad, "")]
            with self.subTest(bad=bad), self.assertRaises(RuntimeError): server.provisioning_list("/api/v1/example", {})
        for url in ("https://evil.example/api/v1/example?page=2", "http://canvas.example.edu/api/v1/example?page=2",
                    "https://canvas.example.edu/api/v1/other?page=2", "https://user@canvas.example.edu/api/v1/example?page=2",
                    "https://canvas.example.edu/api/v1/example?page=2#fragment"):
            self.pages.side_effect = [([], '<'+url+'>; rel="next"')]
            with self.subTest(url=url), self.assertRaises(RuntimeError): server.provisioning_list("/api/v1/example", {})
        link = '<https://canvas.example.edu/api/v1/example?page=opaque>; rel="next"'
        for pages in ([([{"id":1}],link),([{"id":1}],"")], [([],link),([],link)],
                      [([],link+", "+link)], [([], 'invalid; rel="next"')]):
            self.pages.side_effect = pages
            with self.assertRaises(RuntimeError): server.provisioning_list("/api/v1/example", {})
        self.write.assert_not_called()

    def test_uncertain_creation_and_readback_never_retry_or_claim_success(self):
        self.write.side_effect = TimeoutError("unknown")
        for kind in ("course", "instructor"):
            self.write.reset_mock()
            with self.assertRaisesRegex(RuntimeError,"Do not automatically retry"): self.apply(kind)
            self.assertEqual(self.write.call_count,1)
        self.write.side_effect = self.fake_write
        self.course["course_code"]="wrong"
        with self.assertRaisesRegex(RuntimeError,"inspection"): self.apply("course")
        self.course["course_code"]="DS_SAMPLE"
        self.enrollment["role_id"]=61
        with self.assertRaisesRegex(RuntimeError,"inspection"): self.apply("instructor")

    def test_tool_contracts(self):
        tools={t["name"]:t for t in server.TOOLS}
        for name,kind,write in (("canvas_preview_course_creation","course",False),("canvas_create_course_shell","course",True),
            ("canvas_preview_instructor_enrollment","instructor",False),("canvas_enroll_instructor","instructor",True)):
            tool=tools[name]
            self.assertEqual(tool["annotations"]["readOnlyHint"],not write)
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
            expected=server.PROVISIONING_FIELDS[kind] | ({"confirmation"} if write else set())
            self.assertEqual(set(tool["inputSchema"]["properties"]),expected)
            self.assertEqual(set(tool["inputSchema"]["required"]),expected)
            if write: self.assertTrue(tool["annotations"]["destructiveHint"])

    def test_authorization_guidance_reaches_clients_without_granting_permissions(self):
        self.policy["approved_course_creation_targets"] = []
        self.policy["approved_instructor_enrollment_targets"] = []
        before = copy.deepcopy(self.policy)
        initialized = server.handle_request({"id": 1, "method": "initialize"})
        listed = server.handle_request({"id": 2, "method": "tools/list"})
        guidance = server.PROVISIONING_AUTHORIZATION_GUIDANCE
        self.assertIn(guidance, initialized["result"]["instructions"])
        tools = {tool["name"]: tool for tool in listed["result"]["tools"]}
        for name in ("canvas_create_course_shell", "canvas_enroll_instructor"):
            self.assertIn(guidance, tools[name]["description"])
        self.assertEqual(self.policy, before)
        for kind in ("course", "instructor"):
            with self.subTest(kind=kind), self.assertRaises(PermissionError):
                self.apply(kind)
        self.write.assert_not_called()


class PaginationTransportTests(unittest.TestCase):
    def test_link_header_is_retained_case_insensitively_and_query_stays_opaque(self):
        response = io.BytesIO(b'[{"id":1}]')
        response.headers = Message()
        response.headers['lInK'] = '<https://canvas.example.edu/api/v1/example?cursor=next>; rel="next"'
        with mock.patch.object(server, "canvas_base_url", return_value="https://canvas.example.edu"), \
             mock.patch.object(server, "canvas_token", return_value="unit-test-placeholder"), \
             mock.patch.object(server.urllib.request, "urlopen", return_value=response) as network:
            data, links = server.api_get_page("/api/v1/example", "cursor=opaque%2Bvalue")
            self.assertEqual(data, [{"id":1}])
            self.assertIn('rel="next"', links)
            self.assertEqual(network.call_args.args[0].full_url, "https://canvas.example.edu/api/v1/example?cursor=opaque%2Bvalue")
        with self.assertRaises(ValueError):
            server.request_api("POST", "/api/v1/example", _include_links=True)


class ProvisioningPolicyTests(unittest.TestCase):
    def test_migration_and_strict_validation(self):
        keys=["approved_course_creation_account_ids","approved_instructor_enrollment_course_ids","approved_instructor_role_ids"]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"policy.json"; path.touch(); path.chmod(0o600)
            with mock.patch.dict(os.environ,{"CANVAS_WRITE_POLICY":str(path)}):
                for version in (1,2,3,4,5):
                    policy={"version":version,"enabled":True,"approved_course_ids":[123]}
                    if version>=2: policy["approved_rubric_delete_course_ids"]=[]
                    if version>=3: policy["approved_student_deactivation_course_ids"]=[]
                    if version>=4: policy.update({key:[123] for key in keys})
                    if version==5:
                        policy.update(approved_course_creation_targets=[], approved_instructor_enrollment_targets=[])
                    path.write_text(json.dumps(policy))
                    result=server.write_policy()
                    self.assertEqual(result["version"],6)
                    for key in keys: self.assertEqual(result[key],[123] if version>=4 else [])
                for key in keys:
                    for bad in ([True],[0],[-1],[1,1],"1",None):
                        path.write_text(json.dumps({**policy,key:bad}))
                        with self.subTest(key=key,bad=bad),self.assertRaises(server.ConfigurationError):server.write_policy()
                for extra in ({"version":True},{"surprise":[]}, {"enabled":"yes"}):
                    path.write_text(json.dumps({**policy,**extra}))
                    with self.assertRaises(server.ConfigurationError):server.write_policy()

    def test_exact_target_schema_and_preservation(self):
        targets = {
            "approved_course_creation_targets": dict(account_id=10, enrollment_term_id=20, name="DS Sample", course_code="DS_SAMPLE"),
            "approved_instructor_enrollment_targets": dict(course_id=30, section_id=40, canvas_user_id=50,
                                                          sis_user_id="0000000050", role_id=60, role_name="DSTeacher"),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.touch(mode=0o600)
            with mock.patch.dict(os.environ, {"CANVAS_WRITE_POLICY": str(path)}), \
                 mock.patch.object(server, "request_api") as network:
                policy = {**server.disabled_policy(), "enabled": True, "approved_course_ids": [123],
                          **{key: [target] for key, target in targets.items()}}
                path.write_text(json.dumps(policy))
                self.assertEqual(server.write_policy(), policy)
                for key, target in targets.items():
                    bad_values = [None, {}, "*", [None], [target, target], [{**target, "extra": 1}]]
                    for field, value in target.items():
                        bad_values.extend([[{k: v for k, v in target.items() if k != field}],
                                           [{**target, field: True if type(value) is int else " bad"}],
                                           [{**target, field: -1 if type(value) is int else "bad\nvalue"}]])
                    for bad in bad_values:
                        path.write_text(json.dumps({**policy, key: bad}))
                        with self.subTest(key=key, bad=bad), self.assertRaises(server.ConfigurationError):
                            server.write_policy()
                network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
