import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from canvas_mcp import server


class UserUpdateTests(unittest.TestCase):
    def setUp(self):
        self.target = dict(account_id=10, canvas_user_id=20, login_id=30,
                           field='username', expected_value='000020', new_value='person@example.edu')
        self.profile = dict(id=20, name='Example Person', short_name='Example', sortable_name='Person, Example',
                            primary_email='old@example.edu', time_zone='UTC', locale=None)
        self.login = dict(id=30, user_id=20, account_id=10, unique_id='000020', sis_user_id='000020',
                          workflow_state='active', integration_id=None, authentication_provider_id=7,
                          declared_user_type=None, must_reset_password=False)
        self.policy = dict(enabled=True, approved_user_update_targets=[self.target])

    def run_update(self, target=None, after_profile=None, after_logins=None, write_error=None):
        target = target or self.target
        policy = dict(enabled=True, approved_user_update_targets=[target])
        args = {**target, 'confirmation': server.user_update_confirmation(target)}
        with mock.patch.object(server, 'write_policy', return_value=policy), \
             mock.patch.object(server, 'api_get', side_effect=[self.profile, after_profile or self.profile]), \
             mock.patch.object(server, 'provisioning_list', side_effect=[[self.login], after_logins or [self.login]]), \
             mock.patch.object(server, 'request_api', side_effect=write_error) as write:
            result = server.call_tool('canvas_update_user_account', args)
        return result, write

    def test_preview_without_grant_or_write(self):
        with mock.patch.object(server, 'api_get', return_value=self.profile), \
             mock.patch.object(server, 'provisioning_list', return_value=[self.login]), \
             mock.patch.object(server, 'write_policy') as policy, mock.patch.object(server, 'request_api') as write:
            result = server.call_tool('canvas_preview_user_update', self.target)
            self.assertEqual(result['before']['logins'][0], self.login)
            self.assertEqual(result['confirmation'], server.user_update_confirmation(self.target))
            self.assertTrue(result['read_only'])
            write.assert_not_called(); policy.assert_not_called()

    def test_username_only_payload(self):
        result, write = self.run_update(after_logins=[{**self.login, 'unique_id': 'person@example.edu'}])
        write.assert_called_once_with('PUT', '/api/v1/accounts/10/logins/30',
                                      data={'login': {'unique_id': 'person@example.edu'}})
        self.assertTrue(result['verified'])

    def test_email_only_payload(self):
        target = {**self.target, 'field': 'email', 'expected_value': 'old@example.edu', 'new_value': 'new@example.edu'}
        result, write = self.run_update(target, after_profile={**self.profile, 'primary_email': 'new@example.edu'})
        write.assert_called_once_with('PUT', '/api/v1/users/20', data={'user': {'email': 'new@example.edu'}})
        self.assertTrue(result['verified'])

    def test_sis_id_preserves_leading_zeros(self):
        target = {**self.target, 'field': 'sis_user_id', 'new_value': '000099'}
        result, write = self.run_update(target, after_logins=[{**self.login, 'sis_user_id': '000099'}])
        write.assert_called_once_with('PUT', '/api/v1/accounts/10/logins/30', data={'login': {'sis_user_id': '000099'}})
        self.assertEqual(result['current_value'], '000099')

    def test_clear_sis_id(self):
        target = {**self.target, 'field': 'sis_user_id', 'new_value': None}
        result, write = self.run_update(target, after_logins=[{**self.login, 'sis_user_id': None}])
        write.assert_called_once_with('PUT', '/api/v1/accounts/10/logins/30', data={'login': {'sis_user_id': ''}})
        self.assertIsNone(result['current_value'])

    def test_already_requested_value_is_verified_noop(self):
        target = {**self.target, 'new_value': '000020'}
        result, write = self.run_update(target)
        self.assertFalse(result['changed']); write.assert_not_called()

    def test_exact_grant_required_before_network(self):
        for changed in ({'account_id': 11}, {'canvas_user_id': 21}, {'login_id': 31},
                        {'field': 'sis_user_id'}, {'expected_value': 'stale'}, {'new_value': 'other'}):
            target = {**self.target, **changed}
            with self.subTest(changed=changed), mock.patch.object(server, 'write_policy', return_value=self.policy), \
                 mock.patch.object(server, 'request_api') as network, mock.patch.object(server, 'api_get') as read:
                with self.assertRaises(PermissionError):
                    server.update_user_account({**target, 'confirmation': server.user_update_confirmation(target)})
                network.assert_not_called(); read.assert_not_called()

    def test_disabled_empty_policy_and_wrong_confirmation(self):
        for policy, confirmation in ((server.disabled_policy(), server.user_update_confirmation(self.target)),
                                    ({'enabled': True}, server.user_update_confirmation(self.target)),
                                    (self.policy, 'yes')):
            with self.subTest(policy=policy), mock.patch.object(server, 'write_policy', return_value=policy), \
                 mock.patch.object(server, 'api_get') as read:
                with self.assertRaises(PermissionError):
                    server.update_user_account({**self.target, 'confirmation': confirmation})
                read.assert_not_called()

    def test_invalid_or_extra_fields_rejected(self):
        for change in ({'account_id': True}, {'login_id': 0}, {'canvas_user_id': '20'},
                       {'field': 'password'}, {'password': 'not-a-credential'}, {'workflow_state': 'active'},
                       {'path': '/api/v1/users/99'}, {'new_value': 123}, {'new_value': ''},
                       {'new_value': ' padded '}, {'new_value': 'bad\nvalue'}, {'new_value': None},
                       {'field': 'email', 'new_value': 'not-email'}, {'expected_value': False}):
            with self.subTest(change=change), mock.patch.object(server, 'request_api') as network:
                with self.assertRaises(ValueError):
                    server.preview_user_update({**self.target, **change})
                network.assert_not_called()

    def test_identity_account_state_and_stale_value_rejected(self):
        for change in ({'id': 31}, {'user_id': 21}, {'account_id': 11}, {'workflow_state': 'suspended'},
                       {'unique_id': 'changed'}, {'authentication_provider_id': 'MISSING'}):
            login = {**self.login, **change}
            if change.get('authentication_provider_id') == 'MISSING': del login['authentication_provider_id']
            with self.subTest(change=change), mock.patch.object(server, 'api_get', return_value=self.profile), \
                 mock.patch.object(server, 'provisioning_list', return_value=[login]), \
                 mock.patch.object(server, 'request_api') as write, \
                 mock.patch.object(server, 'write_policy', return_value=self.policy):
                with self.assertRaises(ValueError):
                    server.update_user_account({**self.target, 'confirmation': server.user_update_confirmation(self.target)})
                write.assert_not_called()

    def test_unrelated_login_and_profile_fields_protected(self):
        for change in ({'unique_id': 'wrong'}, {'sis_user_id': '999'}, {'authentication_provider_id': 9},
                       {'integration_id': 'changed'}, {'workflow_state': 'suspended'}, {'must_reset_password': True}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.run_update(after_logins=[{**self.login, 'unique_id': 'person@example.edu', **change}])
        with self.assertRaises(RuntimeError):
            self.run_update(after_logins=[{**self.login, 'unique_id': 'person@example.edu'}],
                            after_profile={**self.profile, 'primary_email': 'wrong@example.edu'})
        with self.assertRaises(RuntimeError):
            self.run_update(after_logins=[{**self.login, 'unique_id': 'person@example.edu'}, {**self.login, 'id': 31}])

    def test_uncertain_write_never_retried(self):
        with mock.patch.object(server, 'write_policy', return_value=self.policy), \
             mock.patch.object(server, 'api_get', return_value=self.profile), \
             mock.patch.object(server, 'provisioning_list', return_value=[self.login]), \
             mock.patch.object(server, 'request_api', side_effect=TimeoutError) as write:
            with self.assertRaisesRegex(RuntimeError, 'Do not automatically retry'):
                server.update_user_account({**self.target, 'confirmation': server.user_update_confirmation(self.target)})
            self.assertEqual(write.call_count, 1)

    def test_schema_and_initialization_guidance(self):
        for name, read_only in (('canvas_preview_user_update', True), ('canvas_update_user_account', False)):
            tool = next(t for t in server.TOOLS if t['name'] == name)
            self.assertFalse(tool['inputSchema']['additionalProperties'])
            self.assertEqual(tool['annotations']['readOnlyHint'], read_only)
            self.assertEqual(tool['annotations']['destructiveHint'], not read_only)
        instructions = server.handle_request({'id': 1, 'method': 'initialize'})['result']['instructions']
        self.assertIn(server.API_FIRST_GUIDANCE, instructions)
        self.assertIn('explicit human request', instructions)
        self.assertIn('do not ask the human to repeat approval', instructions)

    def test_policy_validation_and_legacy_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'policy.json'; path.touch(mode=0o600)
            with mock.patch.dict(os.environ, {'CANVAS_WRITE_POLICY': str(path)}):
                current = {**server.disabled_policy(), **self.policy}
                path.write_text(json.dumps(current))
                self.assertEqual(server.write_policy(), current)
                legacy = {key: value for key,value in current.items() if key != 'approved_user_update_targets'}
                legacy['version'] = 5
                path.write_text(json.dumps(legacy))
                self.assertEqual(server.write_policy()['approved_user_update_targets'], [])
                for bad in (None, '*', {}, [self.target, self.target], [{**self.target, 'field': 'password'}]):
                    path.write_text(json.dumps({**current, 'approved_user_update_targets': bad}))
                    with self.subTest(bad=bad), self.assertRaises(server.ConfigurationError): server.write_policy()
                path.write_text(json.dumps(current)); path.chmod(0o644)
                with self.assertRaises(server.ConfigurationError): server.write_policy()
