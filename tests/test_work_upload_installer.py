import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('installer', Path(__file__).resolve().parents[1] / 'install_work_upload_repair.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class WorkUploadInstallerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.source = self.home / 'source'
        self.source.mkdir()
        (self.source / 'pyproject.toml').write_text('[project]\nversion = "0.21.1"\n')
        self.installed = self.home / 'installed'
        self.python = self.installed / '.venv/bin/python'
        config = self.home / '.codex/config.toml'
        config.parent.mkdir()
        config.write_text('[mcp_servers.canvas]\ncommand = ' + json.dumps(str(self.python)))
        folder = self.home / 'canvas-mcp-local'
        folder.mkdir()
        self.root = folder / 'upload-assets'
        self.root.mkdir()
        self.write(folder / 'upload-config.json', {'CANVAS_IMAGE_UPLOAD_ROOT': str(self.root)})
        self.fallback = self.home / '.config/canvas-mcp/upload-config.json'
        self.installed_head = 'old'

    def write(self, path, values):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values))
        path.chmod(0o600)

    def git(self, root, *args):
        if args == ('status', '--porcelain'):
            return ''
        if args == ('remote', 'get-url', 'origin'):
            return 'https://github.com/example/project.git'
        if args == ('rev-parse', 'HEAD'):
            return 'new' if root == self.source else self.installed_head
        if args[:2] == ('merge', '--ff-only'):
            self.installed_head = args[2]
        return ''

    def execute(self, apply=False):
        status = {'connector_version': '0.21.1', 'image_upload_configuration': {'ready': True}}
        response = {'result': {'content': [{'text': json.dumps(status)}]}}
        with patch.object(Path, 'home', return_value=self.home), \
                patch.object(installer, '__file__', str(self.source / 'install_work_upload_repair.py')), \
                patch.object(installer, 'git', side_effect=self.git) as git, \
                patch.object(installer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(response), '')) as run, \
                patch('sys.argv', ['installer'] + (['--apply'] if apply else [])), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            installer.main()
        return output.getvalue(), git, run

    def test_preflight_does_not_change_installation_or_config(self):
        output, git, run = self.execute()
        self.assertIn('Nothing written', output)
        self.assertFalse(self.fallback.exists())
        self.assertEqual(self.installed_head, 'old')
        self.assertEqual(run.call_count, 1)  # Read-only ancestry check.
        self.assertFalse(any(call.args[1] in ['merge', 'fetch'] for call in git.call_args_list))

    def test_manual_apply_fast_forwards_and_preserves_other_root(self):
        previous = {'CANVAS_POWERPOINT_UPLOAD_ROOT': '/existing/presentations'}
        self.write(self.fallback, previous)
        output, git, run = self.execute(apply=True)
        self.assertEqual(self.installed_head, 'new')
        roots = installer.read_private_roots(self.fallback)
        self.assertEqual(roots, dict(previous, CANVAS_IMAGE_UPLOAD_ROOT=str(self.root)))
        self.assertEqual(self.fallback.stat().st_mode & 0o777, 0o600)
        backups = list(self.fallback.parent.glob('upload-config.backup-*.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(installer.read_private_roots(backups[0]), previous)
        self.assertIn('old launch arguments', output)
        self.assertEqual(run.call_args.args[0], [str(self.python), '-m', 'canvas_mcp'])
        for name in ['CANVAS_IMAGE_UPLOAD_ROOT', 'CANVAS_UPLOAD_CONFIG', 'PYTHONPATH']:
            self.assertNotIn(name, run.call_args.kwargs['env'])

    def test_private_roots_reject_unsafe_files_and_fields(self):
        for values in [{'CANVAS_TOKEN': 'fake-test-value'}, {'CANVAS_IMAGE_UPLOAD_ROOT': 'relative'},
                       {'CANVAS_IMAGE_UPLOAD_ROOT': '/path\nwith-control'}]:
            self.write(self.fallback, values)
            with self.assertRaises(ValueError):
                installer.read_private_roots(self.fallback)
        self.write(self.fallback, {})
        self.fallback.chmod(0o644)
        with self.assertRaises(ValueError):
            installer.read_private_roots(self.fallback)
        self.fallback.unlink()
        self.fallback.symlink_to(self.home / 'missing')
        with self.assertRaises(ValueError):
            installer.read_private_roots(self.fallback)
