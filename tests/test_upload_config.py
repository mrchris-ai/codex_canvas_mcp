import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from canvas_mcp import server
from canvas_mcp.upload_config import MAX_UPLOAD_CONFIG_BYTES, UploadConfigError, upload_root_setting


class UploadConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.path = self.home / '.config' / 'canvas-mcp' / 'upload-config.json'
        self.path.parent.mkdir(parents=True)
        self.root = self.home / 'images'
        self.root.mkdir()
        self.image = self.root / 'sample.png'
        self.image.write_bytes(b'\x89PNG\r\n\x1a\nimage-test-data')
        home = patch.object(Path, 'home', return_value=self.home)
        home.start()
        self.addCleanup(home.stop)
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def write(self, values=None, path=None):
        path = path or self.path
        path.write_text(json.dumps(values if values is not None else {
            'CANVAS_IMAGE_UPLOAD_ROOT': str(self.root)}))
        path.chmod(0o600)
        return path

    def test_missing_settings_fail_without_creating_anything(self):
        with self.assertRaisesRegex(server.ConfigurationError, 'configuration file is missing'):
            server.image_upload_root()
        self.assertFalse(self.path.exists())
        self.assertEqual(dict(os.environ), {})

    def test_private_default_restores_root_but_not_write_authorization(self):
        self.write()
        self.assertEqual(server.image_upload_root(), self.root.resolve())
        self.assertEqual(server.validate_image_file(str(self.image))[1], 'image/png')
        self.assertEqual(dict(os.environ), {})
        with patch.object(server, 'canvas_token') as token, patch.object(server.urllib.request, 'urlopen') as network:
            with self.assertRaises(PermissionError):
                server.call_tool('canvas_upload_image', {
                    'course_id': 123, 'file_path': str(self.image),
                    'confirmation': 'APPROVE CANVAS IMAGE UPLOAD course 123'})
            token.assert_not_called()
            network.assert_not_called()

    def test_explicit_root_wins_even_if_invalid_without_consulting_file(self):
        self.write()
        os.environ['CANVAS_IMAGE_UPLOAD_ROOT'] = 'relative'
        with patch('canvas_mcp.upload_config.os.open') as opened:
            with self.assertRaises(server.ConfigurationError):
                server.image_upload_root()
            opened.assert_not_called()

    def test_explicit_config_never_falls_back(self):
        self.write()
        os.environ['CANVAS_UPLOAD_CONFIG'] = str(self.home / 'missing.json')
        with self.assertRaises(server.ConfigurationError):
            server.image_upload_root()
        os.environ['CANVAS_UPLOAD_CONFIG'] = 'relative.json'
        with self.assertRaises(server.ConfigurationError):
            server.image_upload_root()

    def test_blank_root_uses_file_and_changes_take_effect_per_call(self):
        self.write()
        os.environ['CANVAS_IMAGE_UPLOAD_ROOT'] = '  '
        self.assertEqual(server.image_upload_root(), self.root.resolve())
        self.write({'CANVAS_POWERPOINT_UPLOAD_ROOT': str(self.root)})
        self.assertEqual(upload_root_setting('CANVAS_POWERPOINT_UPLOAD_ROOT'), str(self.root))
        with self.assertRaises(server.ConfigurationError):
            server.image_upload_root()
        self.path.unlink()
        with self.assertRaises(server.ConfigurationError):
            server.image_upload_root()

    def test_rooted_signature_size_and_exact_write_gates_still_apply(self):
        self.write()
        outside = self.home / 'outside.png'
        outside.write_bytes(self.image.read_bytes())
        with self.assertRaises(PermissionError):
            server.validate_image_file(str(outside))
        link = self.root / 'link.png'
        link.symlink_to(self.image)
        with self.assertRaises(ValueError):
            server.validate_image_file(str(link))
        self.image.write_bytes(b'not-a-png')
        with self.assertRaises(ValueError):
            server.validate_image_file(str(self.image))
        self.image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'x' * server.MAX_IMAGE_UPLOAD_BYTES)
        with self.assertRaises(ValueError):
            server.validate_image_file(str(self.image))
        policy = server.disabled_policy()
        policy.update(enabled=True, approved_course_ids=[123])
        with patch.object(server, 'write_policy', return_value=policy):
            server.require_image_upload_approval(123, 'APPROVE CANVAS IMAGE UPLOAD course 123')
            for course, confirmation in [(456, 'APPROVE CANVAS IMAGE UPLOAD course 456'), (123, 'yes')]:
                with self.assertRaises(PermissionError):
                    server.require_image_upload_approval(course, confirmation)

    def test_private_config_rejects_modes_owners_symlinks_fifos_and_directories(self):
        self.write()
        for mode in [0o644, 0o666, 0o700, 0o400]:
            self.path.chmod(mode)
            with self.subTest(mode=mode), self.assertRaises(server.ConfigurationError):
                server.image_upload_root()
        self.path.chmod(0o600)
        with patch('canvas_mcp.upload_config.os.getuid', return_value=os.getuid() + 1):
            with self.assertRaises(server.ConfigurationError):
                server.image_upload_root()
        target = self.write(path=self.home / 'target.json')
        self.path.unlink()
        self.path.symlink_to(target)
        with self.assertRaises(server.ConfigurationError):
            server.image_upload_root()
        target.unlink()
        with self.assertRaises(server.ConfigurationError):
            server.image_upload_root()
        self.path.unlink()
        os.mkfifo(self.path, 0o600)
        with self.assertRaises(server.ConfigurationError):
            server.image_upload_root()
        self.path.unlink()
        self.path.mkdir()
        with self.assertRaises(server.ConfigurationError):
            server.image_upload_root()

    def test_rejects_malformed_duplicate_large_or_unsupported_config(self):
        self.write()
        for raw in [b'not-json', b'\xff', b'[]', b'null',
                    b'{"CANVAS_IMAGE_UPLOAD_ROOT":"/one","CANVAS_IMAGE_UPLOAD_ROOT":"/two"}',
                    b' ' * (MAX_UPLOAD_CONFIG_BYTES + 1)]:
            self.path.write_bytes(raw)
            with self.subTest(raw=raw[:20]), self.assertRaises(server.ConfigurationError):
                server.image_upload_root()
        for key in ['CANVAS_WRITE_POLICY', 'enabled', 'approved_course_ids', 'CANVAS_TOKEN', 'PATH']:
            self.write({'CANVAS_IMAGE_UPLOAD_ROOT': str(self.root), key: 'fake-test-value'})
            with self.subTest(key=key), self.assertRaises(server.ConfigurationError):
                server.image_upload_root()
        self.assertEqual(dict(os.environ), {})

    def test_all_values_are_validated_without_echoing_them(self):
        for value in ['', 'relative', None, 123, '/bad\npath', '/bad\ud800path']:
            self.write({'CANVAS_IMAGE_UPLOAD_ROOT': str(self.root), 'CANVAS_POWERPOINT_UPLOAD_ROOT': value})
            with self.subTest(value=value), self.assertRaises(server.ConfigurationError):
                server.image_upload_root()
        with self.assertRaises(UploadConfigError):
            upload_root_setting('PATH')
        with patch('canvas_mcp.upload_config.os.open', side_effect=PermissionError):
            with self.assertRaises(server.ConfigurationError):
                server.image_upload_root()

    def test_cli_argument_supports_host_missing_environment(self):
        explicit = self.write(path=self.home / 'explicit.json')
        request = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'
        with patch('sys.argv', ['canvas_mcp', '--upload-config', str(explicit)]), \
                patch('sys.stdin', io.StringIO(request)), patch('sys.stdout', new_callable=io.StringIO) as output:
            server.main()
        self.assertEqual(json.loads(output.getvalue())['id'], 1)
        self.assertEqual(server.image_upload_root(), self.root.resolve())
        with patch('sys.argv', ['canvas_mcp', '--upload-config', 'relative.json']), \
                patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit):
            server.main()
