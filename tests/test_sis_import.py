import copy
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from canvas_mcp import server, sis_import

REAL_POLICY = server.write_policy


class SisImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.target = {"account_id": 10, "course_id": 30, "section_id": 40,
                       "sis_course_id": "SIS_30", "sis_section_id": None, "role_id": 60,
                       "students": [{"canvas_user_id": 50, "sis_user_id": "0001234567"}]}
        self.record = {"id": 70, "course_id": 30, "course_section_id": 40, "user_id": 50,
                       "sis_user_id": "0001234567", "sis_course_id": "SIS_30", "role_id": 60,
                       "role": "StudentEnrollment", "type": "StudentEnrollment",
                       "limit_privileges_to_course_section": False, "enrollment_state": "active",
                       "sis_import_id": 80}
        self.records = []
        self.sections = [{"id": 40, "course_id": 30, "sis_section_id": None}]
        self.responses = {
            "/api/v1/accounts/10": {"id": 10, "workflow_state": "active", "parent_account_id": None},
            "/api/v1/accounts/10/permissions": {"manage_sis": True},
            "/api/v1/courses/30": {"id": 30, "account_id": 20, "root_account_id": 10,
                                   "workflow_state": "available", "sis_course_id": "SIS_30", "name": "Test"},
            "/api/v1/sections/40": self.sections[0],
            "/api/v1/users/50/profile": {"id": 50, "sis_user_id": "0001234567", "name": "Test Student"},
            "/api/v1/users/sis_user_id:0001234567/profile": {"id": 50, "sis_user_id": "0001234567"},
            "/api/v1/accounts/10/sis_imports/80": {"id": 80, "workflow_state": "imported", "batch_mode": False,
                "data": {"import_type": "instructure_csv", "supplied_batches": ["enrollment"],
                         "counts": {"enrollments": 1, "error_count": 0, "warning_count": 0}}},
        }
        self.policy = {**server.disabled_policy(), "enabled": True}
        mock.patch.object(Path, "home", return_value=Path(self.directory.name)).start()
        mock.patch.object(server, "canvas_base_url", return_value="https://canvas.example.edu").start()
        mock.patch.object(server, "canvas_token", side_effect=AssertionError("No live credentials")).start()
        self.get = mock.patch.object(server, "api_get", side_effect=lambda path, query=None: copy.deepcopy(self.responses[path])).start()
        self.list = mock.patch.object(server, "provisioning_list", side_effect=self.fake_list).start()
        self.post = mock.patch.object(sis_import, "post_csv", side_effect=self.fake_post).start()
        mock.patch.object(server, "write_policy", return_value=self.policy).start()
        self.addCleanup(mock.patch.stopall)
        self.policy["approved_sis_enrollment_import_fingerprints"] = [sis_import.fingerprint(sis_import.target(self.target))]

    def fake_list(self, path, query):
        if path == "/api/v1/courses/30/sections":
            return copy.deepcopy(self.sections)
        if path == "/api/v1/accounts/20/roles":
            return [{"id": 60, "role": "StudentEnrollment", "label": "Student",
                     "base_role_type": "StudentEnrollment", "workflow_state": "built_in"}]
        self.assertEqual(path, "/api/v1/courses/30/enrollments")
        self.assertIn(query["user_id"], (50, 51))
        return copy.deepcopy([r for r in self.records if r['user_id'] == query['user_id']])

    def fake_post(self, account_id, payload):
        self.assertEqual(account_id, 10)
        rows = list(csv.DictReader(io.StringIO(payload.decode())))
        self.assertEqual(rows, [{"course_id": "SIS_30", "user_id": "0001234567", "role": "student",
                                "section_id": "", "status": "active", "notify": "false"}])
        self.records = [copy.deepcopy(self.record)]
        return {"id": 80, "workflow_state": "created"}

    def submit(self):
        preview = sis_import.preview(self.target)
        return sis_import.submit({**self.target, "confirmation": preview["confirmation"]})

    def test_submit_is_pending_until_import_and_enrollment_are_verified(self):
        result = self.submit()
        self.assertTrue(result["submitted"])
        self.assertFalse(result["verified"])
        status = self.responses["/api/v1/accounts/10/sis_imports/80"]
        status["workflow_state"] = "importing"
        pending = sis_import.verify({**self.target, "import_id": 80})
        self.assertTrue(pending["pending"])
        status["workflow_state"] = "imported"
        verified = sis_import.verify({**self.target, "import_id": 80})
        self.assertTrue(verified["verified"])
        self.assertEqual(verified["students"][0]["sis_user_id"], "0001234567")
        self.post.assert_called_once()

    def test_preview_is_read_only_and_csv_is_enrollment_only(self):
        p = sis_import.preview(self.target)
        self.assertEqual(p["import_row_count"], 1)
        self.assertIn("0001234567", p["csv"])
        self.assertFalse((Path(self.directory.name) / '.canvas-mcp-sis-imports').exists())
        self.post.assert_not_called()

    def test_two_students_and_mixed_noop_batch_are_verified_individually(self):
        student = {'canvas_user_id': 51, 'sis_user_id': '0001234568'}
        self.target['students'].append(student)
        self.responses['/api/v1/users/51/profile'] = {'id': 51, 'sis_user_id': student['sis_user_id'], 'name': 'Second Student'}
        self.responses['/api/v1/users/sis_user_id:0001234568/profile'] = self.responses['/api/v1/users/51/profile']
        second = self.record | {'id': 71, 'user_id': 51, 'sis_user_id': student['sis_user_id']}
        self.policy['approved_sis_enrollment_import_fingerprints'] = [sis_import.fingerprint(sis_import.target(self.target))]
        for already_active in (False, True):
            with self.subTest(already_active=already_active):
                # Each subcase has an isolated receipt directory, as independent operator runs do.
                with tempfile.TemporaryDirectory() as isolated, mock.patch.object(Path, 'home', return_value=Path(isolated)):
                    self.records = [self.record] if already_active else []
                    expected_ids = ['0001234568'] if already_active else ['0001234567', '0001234568']
                    def post(account_id, payload):
                        rows = list(csv.DictReader(io.StringIO(payload.decode())))
                        self.assertEqual([r['user_id'] for r in rows], expected_ids)
                        self.records = [self.record, second]
                        return {'id': 80}
                    self.post.side_effect = post
                    self.responses['/api/v1/accounts/10/sis_imports/80']['data']['counts']['enrollments'] = len(expected_ids)
                    self.submit()
                    verified = sis_import.verify(self.target | {'import_id': 80})
                    self.assertEqual([r['changed'] for r in verified['students']], [not already_active, True])
                    self.assertEqual(len(verified['students']), 2)

    def test_policy_confirmation_origin_and_payload_are_bound(self):
        p = sis_import.preview(self.target)
        for policy in ({**self.policy, "enabled": False}, {**self.policy, "approved_sis_enrollment_import_fingerprints": []}):
            with mock.patch.object(server, "write_policy", return_value=policy), self.assertRaises(PermissionError):
                sis_import.submit({**self.target, "confirmation": p["confirmation"]})
        with self.assertRaises(PermissionError):
            sis_import.submit({**self.target, "confirmation": "wrong"})
        with mock.patch.object(server, "canvas_base_url", return_value="https://other.example.edu"), self.assertRaises(PermissionError):
            sis_import.submit({**self.target, "confirmation": p["confirmation"]})
        self.records = [self.record]
        with self.assertRaises(PermissionError):
            sis_import.submit({**self.target, "confirmation": p["confirmation"]})
        self.post.assert_not_called()

    def test_existing_active_is_noop_and_exact_deleted_can_be_imported(self):
        self.records = [copy.deepcopy(self.record)]
        result = self.submit()
        self.assertTrue(result["verified"])
        self.assertFalse(result["submitted"])
        self.post.assert_not_called()
        self.records[0]["enrollment_state"] = "deleted"
        self.assertTrue(self.submit()["submitted"])
        self.assertTrue(sis_import.verify({**self.target, "import_id": 80})["verified"])

    def test_conflicting_or_invited_enrollment_never_writes(self):
        for change in ({"enrollment_state": "invited"}, {"enrollment_state": "inactive"},
                       {"enrollment_state": "completed"}, {"role_id": 61}, {"course_section_id": 41}):
            with self.subTest(change=change):
                self.records = [{**self.record, **change}]
                with self.assertRaises(ValueError):
                    self.submit()
        self.post.assert_not_called()

    def test_preflight_rejects_wrong_account_identity_role_section_and_permission(self):
        changes = [('/api/v1/accounts/10', {'parent_account_id': 1}),
                   ('/api/v1/accounts/10/permissions', {'manage_sis': False}),
                   ('/api/v1/courses/30', {'root_account_id': 11}),
                   ('/api/v1/courses/30', {'sis_course_id': 'different'}),
                   ('/api/v1/sections/40', {'sis_section_id': 'different'}),
                   ('/api/v1/sections/40', {'nonxlist_course_id': 31}),
                   ('/api/v1/users/50/profile', {'sis_user_id': 'different'}),
                   ('/api/v1/users/sis_user_id:0001234567/profile', {'id': 51})]
        for path, values in changes:
            old = copy.deepcopy(self.responses[path])
            with self.subTest(path=path, values=values):
                self.responses[path].update(values)
                with self.assertRaises((ValueError, PermissionError)):
                    self.submit()
                self.responses[path] = old
        with mock.patch.object(server, 'provisioning_list', side_effect=lambda path, query: [] if path.endswith('/roles') else self.fake_list(path, query)):
            with self.assertRaises(ValueError): self.submit()
        self.post.assert_not_called()

    def test_missing_sis_section_requires_sole_default(self):
        self.sections.append({'id': 41, 'course_id': 30})
        with self.assertRaises(ValueError): self.submit()
        self.post.assert_not_called()

    def test_explicit_sis_section_is_in_generated_csv(self):
        self.target['sis_section_id'] = 'SECTION_40'
        self.responses['/api/v1/sections/40']['sis_section_id'] = 'SECTION_40'
        preview = sis_import.preview(self.target)
        self.assertIn('student,SECTION_40,active,false', preview['csv'])

    def test_duplicate_unknown_and_unsafe_input_refused(self):
        bad = [{**self.target, 'batch_mode': True}, {**self.target, 'students': []},
               {**self.target, 'students': self.target['students'] * 2},
               {**self.target, 'students': self.target['students'] * 26},
               {**self.target, 'course_id': True}, {**self.target, 'sis_course_id': 'bad\nrow'},
               {**self.target, 'students': [{'canvas_user_id': 50, 'sis_user_id': 1234567}]},
               {**self.target, 'students': [{'canvas_user_id': 50, 'sis_user_id': 'x', 'role': 'teacher'}]}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError): sis_import.preview(value)
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_timeout_keeps_private_receipt_and_blocks_retry(self):
        self.post.side_effect = TimeoutError('uncertain')
        with self.assertRaisesRegex(RuntimeError, 'No automatic retry'): self.submit()
        digest = sis_import.fingerprint(sis_import.target(self.target))
        receipt = sis_import.read_receipt(digest)
        self.assertIsNone(receipt['import_id'])
        self.assertEqual(sis_import.receipt_path(digest).stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(RuntimeError, 'already has a submission receipt'): self.submit()
        self.post.assert_called_once()

    def test_duplicate_submit_and_wrong_import_id_are_blocked(self):
        self.submit()
        with self.assertRaisesRegex(RuntimeError, 'submission receipt'): self.submit()
        with self.assertRaises(ValueError): sis_import.verify({**self.target, 'import_id': 81})
        self.post.assert_called_once()

    def test_terminal_errors_counts_and_readback_fail_closed(self):
        self.submit()
        original = copy.deepcopy(self.responses['/api/v1/accounts/10/sis_imports/80'])
        for change in ({'workflow_state': 'failed'}, {'workflow_state': 'imported_with_messages'},
                       {'processing_warnings': [['enrollments.csv', 'warning']]}, {'batch_mode': True},
                       {'data': {**original['data'], 'counts': {'enrollments': 2}}},
                       {'data': {**original['data'], 'supplied_batches': ['user', 'enrollment']}}):
            self.responses['/api/v1/accounts/10/sis_imports/80'] = original | change
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                sis_import.verify({**self.target, 'import_id': 80})
        self.responses['/api/v1/accounts/10/sis_imports/80'] = original
        for change in ({'sis_import_id': 81}, {'enrollment_state': 'invited'}, {'user_id': 51}, {'course_section_id': 41}):
            self.records = [self.record | change]
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                sis_import.verify({**self.target, 'import_id': 80})
        self.post.assert_called_once()

    def test_receipt_symlink_and_permissions_fail_closed(self):
        digest = sis_import.fingerprint(sis_import.target(self.target))
        directory = Path(self.directory.name) / '.canvas-mcp-sis-imports'
        other = Path(self.directory.name) / 'other'; other.mkdir(mode=0o700)
        directory.symlink_to(other, target_is_directory=True)
        with self.assertRaises(server.ConfigurationError): self.submit()
        directory.unlink(); directory.mkdir(mode=0o700)
        destination = other / 'receipt'; destination.write_text('{}'); destination.chmod(0o600)
        sis_import.receipt_path(digest).symlink_to(destination)
        with self.assertRaises(OSError): self.submit()
        self.post.assert_not_called()

    def test_tools_are_registered_with_separate_write_annotation(self):
        tools = {t['name']: t for t in server.TOOLS}
        for action in ('preview', 'submit', 'verify'):
            self.assertEqual(tools[f'canvas_{action}_sis_enrollment_import']['annotations']['readOnlyHint'], action != 'submit')
        self.assertTrue(server.call_tool('canvas_preview_sis_enrollment_import', self.target)['read_only'])

    def test_policy_v10_and_legacy_migration(self):
        path = Path(self.directory.name) / 'policy.json'; path.touch(mode=0o600)
        with mock.patch.dict(os.environ, {'CANVAS_WRITE_POLICY': str(path)}):
            path.write_text(json.dumps(self.policy)); self.assertEqual(REAL_POLICY(), self.policy)
            old = {k: v for k, v in self.policy.items()
                   if k not in {'approved_sis_enrollment_import_fingerprints', 'approved_student_reactivation_fingerprints', 'approved_student_enrollment_deletion_fingerprints'}}
            old['version'] = 9; path.write_text(json.dumps(old))
            self.assertEqual(REAL_POLICY()['approved_sis_enrollment_import_fingerprints'], [])
            for bad in ([True], ['*'], ['a'*64, 'a'*64], 'a'*64):
                path.write_text(json.dumps(self.policy | {'approved_sis_enrollment_import_fingerprints': bad}))
                with self.subTest(bad=bad), self.assertRaises(server.ConfigurationError): REAL_POLICY()


class SisTransportTests(unittest.TestCase):
    def test_transport_is_fixed_endpoint_utf8_and_has_no_bulk_options(self):
        response = mock.MagicMock(); response.__enter__.return_value = io.BytesIO(b'{"id":80}')
        opener = mock.Mock(); opener.open.return_value = response
        with mock.patch.object(server, 'canvas_base_url', return_value='https://canvas.example.edu'), \
             mock.patch.object(server, 'canvas_token', return_value='mock-token'), \
             mock.patch.object(sis_import.urllib.request, 'build_opener', return_value=opener) as build:
            result = sis_import.post_csv(10, b'generated,csv\n')
        self.assertEqual(result['id'], 80)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://canvas.example.edu/api/v1/accounts/10/sis_imports?import_type=instructure_csv')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Content-type'), 'text/csv')
        self.assertEqual(request.data, b'generated,csv\n')
        self.assertTrue(any(isinstance(h, sis_import.NoRedirect) for h in build.call_args.args))
        with self.assertRaises(RuntimeError):
            sis_import.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example.edu')


if __name__ == '__main__':
    unittest.main()
