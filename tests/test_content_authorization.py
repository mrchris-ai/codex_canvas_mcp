import os
from pathlib import Path
import stat
import tempfile
import tomllib
import unittest
from unittest import mock

from canvas_mcp import server
from canvas_mcp.content_authorization import (
    CONTENT_AUTHORIZATION_GUIDANCE, CONTENT_WRITE_TOOLS,
    configure_client_approvals, prepare_client_config,
)


CONFIG = '''# Preserve this comment.
model = "unchanged"
[mcp_servers.canvas]
command = "python"
args = ["-m", "canvas_mcp"]
default_tools_approval_mode = "writes"
[mcp_servers.canvas.env]
CANVAS_BASE_URL = "https://canvas.example.edu"
[mcp_servers.canvas.tools.canvas_create_user_account]
approval_mode = "prompt"
[mcp_servers.other]
command = "unchanged"
'''


class ContentAuthorizationTests(unittest.TestCase):
    def test_only_content_tool_preferences_change_and_reapplication_is_noop(self):
        changed = prepare_client_config(CONFIG)
        self.assertTrue(changed.startswith(CONFIG))
        parsed = tomllib.loads(changed)
        for name in CONTENT_WRITE_TOOLS:
            self.assertEqual(parsed['mcp_servers']['canvas']['tools'].pop(name), {'approval_mode': 'approve'})
        self.assertEqual(parsed, tomllib.loads(CONFIG))
        self.assertEqual(prepare_client_config(changed), changed)

    def test_conflicting_override_is_not_silently_replaced(self):
        raw = CONFIG + '\n[mcp_servers.canvas.tools.canvas_write_page]\napproval_mode="prompt"\n'
        with self.assertRaises(ValueError):
            prepare_client_config(raw)

    def test_missing_registration_does_not_create_one(self):
        with self.assertRaises(ValueError):
            prepare_client_config('model="unchanged"')

    def test_dry_run_and_apply_preserve_private_file_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.toml'
            path.write_text(CONFIG)
            path.chmod(0o600)
            self.assertTrue(configure_client_approvals(path))
            self.assertEqual(path.read_text(), CONFIG)
            self.assertTrue(configure_client_approvals(path, apply=True))
            self.assertEqual(path.read_text(), prepare_client_config(CONFIG))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertFalse(configure_client_approvals(path, apply=True))

    def test_symlink_and_insecure_file_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.toml'
            path.write_text(CONFIG)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                configure_client_approvals(path, apply=True)
            path.chmod(0o600)
            alias = Path(tmp) / 'alias.toml'
            alias.symlink_to(path)
            with self.assertRaises(OSError):
                configure_client_approvals(alias, apply=True)
            self.assertEqual(path.read_text(), CONFIG)

    def test_replace_failure_preserves_original_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.toml'
            path.write_text(CONFIG)
            path.chmod(0o600)
            with mock.patch('canvas_mcp.content_authorization.os.replace', side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    configure_client_approvals(path, apply=True)
            self.assertEqual(path.read_text(), CONFIG)
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_overrides_are_real_content_mutations_not_admin_or_reads(self):
        tools = {t['name']: t for t in server.TOOLS}
        for name in CONTENT_WRITE_TOOLS:
            tool = tools[name]
            self.assertFalse(tool['annotations']['readOnlyHint'])
            self.assertTrue(tool['annotations']['destructiveHint'])
            self.assertIn(CONTENT_AUTHORIZATION_GUIDANCE, tool['description'])
        for name in ('canvas_delete_rubric', 'canvas_create_course_shell', 'canvas_enroll_student',
                     'canvas_enroll_instructor', 'canvas_deactivate_student_enrollment',
                     'canvas_reactivate_student_enrollment', 'canvas_preview_student_reactivation',
                     'canvas_delete_student_enrollment', 'canvas_preview_student_enrollment_deletion',
                     'canvas_create_user_account', 'canvas_update_user_account', 'canvas_read_api'):
            self.assertNotIn(name, CONTENT_WRITE_TOOLS)

    def test_approved_task_still_cannot_write_to_unlisted_course(self):
        args = {'course_id': 456, 'name': 'Example', 'confirmation': 'APPROVE CANVAS MODULE WRITE course 456'}
        with mock.patch.object(server, 'write_policy', return_value={'enabled': True, 'approved_course_ids': [123]}), \
                mock.patch.object(server, 'request_api') as network:
            with self.assertRaises(PermissionError):
                server.call_tool('canvas_create_module', args)
            network.assert_not_called()

    def test_approved_course_uses_technical_confirmation_without_new_prompt(self):
        args = {'course_id': 123, 'name': 'Example', 'confirmation': 'APPROVE CANVAS MODULE WRITE course 123'}
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                   'params': {'name': 'canvas_create_module', 'arguments': args}}
        with mock.patch.object(server, 'write_policy', return_value={'enabled': True, 'approved_course_ids': [123]}), \
                mock.patch.object(server, 'request_api', return_value={'id': 789}) as network, \
                mock.patch.object(server, 'respond') as output:
            result = server.handle_request(request)
            self.assertIn('result', result)
            network.assert_called_once()
            output.assert_not_called()  # No per-write elicitation/request is emitted.


if __name__ == '__main__':
    unittest.main()
