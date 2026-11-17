import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'launcher_config', Path(__file__).resolve().parents[1] / 'configure_workspace_launcher.py')
launcher_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher_config)


class WorkspaceLauncherConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.checkout = self.home / 'checkout'
        self.checkout.mkdir()
        (self.checkout / 'run_canvas_mcp.py').touch()
        upload = self.home / 'canvas-mcp-local'
        upload.mkdir()
        (upload / 'upload-assets').mkdir()
        (upload / 'upload-config.json').write_text(json.dumps({
            'CANVAS_IMAGE_UPLOAD_ROOT': str(upload / 'upload-assets')}))
        self.config = self.home / '.codex' / 'config.toml'
        self.config.parent.mkdir()
        self.original = '''other = "preserve"
[mcp_servers.canvas]
command = "/existing/python"
args = ["-m", "canvas_mcp", "--connection-config", "/existing/connection.json"]
[mcp_servers.canvas.env]
CANVAS_IMAGE_UPLOAD_ROOT = "/old/root"
CANVAS_WRITE_POLICY = "/existing/policy.json"
[mcp_servers.other]
command = "preserve-other-server"
'''
        self.config.write_text(self.original)
        self.config.chmod(0o600)

    def run_main(self, args):
        with patch.object(Path, 'home', return_value=self.home), \
                patch.object(launcher_config, '__file__', str(self.checkout / 'configure_workspace_launcher.py')), \
                patch('sys.argv', ['configure_workspace_launcher.py'] + args), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            launcher_config.main()
        return output.getvalue()

    def test_default_only_validates_without_writing(self):
        self.assertIn('Nothing written', self.run_main([]))
        self.assertEqual(self.config.read_text(), self.original)
        self.assertEqual(len(list(self.config.parent.iterdir())), 1)

    def test_explicit_apply_preserves_unrelated_settings_and_private_backup(self):
        self.assertIn('saved and verified', self.run_main(['--apply']))
        parsed = tomllib.loads(self.config.read_text())
        self.assertEqual(parsed['other'], 'preserve')
        self.assertEqual(parsed['mcp_servers']['other']['command'], 'preserve-other-server')
        canvas = parsed['mcp_servers']['canvas']
        self.assertEqual(canvas['command'], '/existing/python')
        self.assertEqual(canvas['env']['CANVAS_WRITE_POLICY'], '/existing/policy.json')
        self.assertEqual(canvas['args'][0], str((self.checkout / 'run_canvas_mcp.py').resolve()))
        backups = list(self.config.parent.glob('config.toml.canvas-backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), self.original)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertIn('already verified', self.run_main(['--apply']))
        self.assertEqual(len(list(self.config.parent.glob('config.toml.canvas-backup-*'))), 1)

    def test_unexpected_launcher_fails_without_changes(self):
        unexpected = self.original.replace('"-m", "canvas_mcp"', '"different-launcher"')
        self.config.write_text(unexpected)
        with self.assertRaises(ValueError):
            self.run_main(['--apply'])
        self.assertEqual(self.config.read_text(), unexpected)
