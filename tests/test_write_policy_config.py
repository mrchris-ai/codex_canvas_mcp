import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from canvas_mcp import server


class WritePolicyConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.default = self.home / '.config' / 'canvas-mcp' / 'write-policy.json'
        self.default.parent.mkdir(parents=True)
        home = patch.object(Path, 'home', return_value=self.home)
        home.start()
        self.addCleanup(home.stop)
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def write(self, path=None, enabled=True, courses=None):
        path = path or self.default
        path.write_text(json.dumps({'version': 1, 'enabled': enabled,
                                    'approved_course_ids': courses or [123]}))
        path.chmod(0o600)
        return path

    def test_missing_policy_is_disabled_and_creates_nothing(self):
        self.assertEqual(server.write_policy(), server.disabled_policy())
        self.assertFalse(self.default.exists())

    def test_private_default_supports_cached_environment_and_exact_gate(self):
        self.write()
        server.require_write_approval(123, 'APPROVE CANVAS PAGE WRITE course 123', 'PAGE')
        with self.assertRaises(PermissionError):
            server.require_write_approval(456, 'APPROVE CANVAS PAGE WRITE course 456', 'PAGE')
        with self.assertRaises(PermissionError):
            server.require_write_approval(123, 'yes', 'PAGE')
        self.assertEqual(server.write_policy()['approved_rubric_delete_course_ids'], [])

    def test_default_edits_and_removal_take_effect_without_restart(self):
        self.write()
        self.assertTrue(server.write_policy()['enabled'])
        self.write(enabled=False)
        self.assertFalse(server.write_policy()['enabled'])
        self.default.unlink()
        self.assertFalse(server.write_policy()['enabled'])

    def test_explicit_path_wins_and_never_falls_back(self):
        self.write()
        explicit = self.home / 'explicit.json'
        os.environ['CANVAS_WRITE_POLICY'] = str(explicit)
        self.assertFalse(server.write_policy()['enabled'])
        self.write(explicit, enabled=False, courses=[456])
        self.assertEqual(server.write_policy()['approved_course_ids'], [456])
        self.assertFalse(server.write_policy()['enabled'])
        explicit.write_text('invalid')
        with self.assertRaises(server.ConfigurationError):
            server.write_policy()

    def test_blank_environment_uses_default(self):
        self.write()
        os.environ['CANVAS_WRITE_POLICY'] = '  '
        self.assertTrue(server.write_policy()['enabled'])

    def test_insecure_modes_and_foreign_owner_fail_closed(self):
        self.write()
        for mode in [0o644, 0o666, 0o700, 0o400]:
            self.default.chmod(mode)
            with self.subTest(mode=mode), self.assertRaises(server.ConfigurationError):
                server.write_policy()
        self.default.chmod(0o600)
        with patch.object(server.os, 'getuid', return_value=os.getuid() + 1):
            with self.assertRaises(server.ConfigurationError):
                server.write_policy()

    def test_symlinks_directories_and_fifos_fail_closed(self):
        target = self.write(self.home / 'target.json')
        self.default.symlink_to(target)
        with self.assertRaises(server.ConfigurationError):
            server.write_policy()
        target.unlink()
        with self.assertRaises(server.ConfigurationError):
            server.write_policy()
        self.default.unlink()
        self.default.mkdir(mode=0o700)
        with self.assertRaises(server.ConfigurationError):
            server.write_policy()
        self.default.rmdir()
        os.mkfifo(self.default, 0o600)
        with self.assertRaises(server.ConfigurationError):
            server.write_policy()

    def test_malformed_duplicate_oversized_and_unknown_fields_fail_closed(self):
        self.write()
        for raw in [b'not-json', b'\xff', b'[]',
                    b'{"version":1,"enabled":false,"enabled":true,"approved_course_ids":[123]}',
                    b'{"version":1,"enabled":true,"approved_course_ids":[123],"extra":true}',
                    b' ' * (server.MAX_WRITE_POLICY_BYTES + 1)]:
            self.default.write_bytes(raw)
            with self.subTest(raw=raw[:20]), self.assertRaises(server.ConfigurationError):
                server.write_policy()

    def test_inaccessible_file_fails_closed(self):
        with patch.object(Path, 'lstat', side_effect=PermissionError):
            with self.assertRaises(server.ConfigurationError):
                server.write_policy()
