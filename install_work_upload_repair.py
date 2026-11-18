"""Install the reviewed repair at Work's existing cached connector path.

The default is a read-only preflight. --apply is for a user-run Terminal when
the authoring host denies writes to the installation. It does not bypass host
restrictions, change authorization policy, terminate processes, or call Canvas.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import tomllib


KEYS = {'CANVAS_IMAGE_UPLOAD_ROOT', 'CANVAS_POWERPOINT_UPLOAD_ROOT'}


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate upload configuration key.')
        result[key] = value
    return result


def read_private_roots(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError('Upload configuration must not be a symlink.')
        return None
    except OSError as exc:
        raise ValueError('Cannot securely open the private upload configuration.') from exc
    with os.fdopen(fd, 'rb') as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('Upload configuration must be an owned regular file with mode 0600.')
        raw = f.read(16385)
    if len(raw) > 16384:
        raise ValueError('Upload configuration is too large.')
    values = json.loads(raw, object_pairs_hook=unique_keys)
    if not isinstance(values, dict) or set(values) - KEYS:
        raise ValueError('Unsupported upload configuration fields.')
    for value in values.values():
        if (not isinstance(value, str) or not Path(value).is_absolute()
                or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
            raise ValueError('Upload roots must be absolute paths without control characters.')
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    config = tomllib.loads((Path.home() / '.codex' / 'config.toml').read_text())
    python = Path(config['mcp_servers']['canvas']['command'])
    if python.parent.name != 'bin' or python.parent.parent.name != '.venv':
        raise ValueError('Expected the existing connector virtual-environment command.')
    installed = python.parent.parent.parent
    if source == installed or git(installed, 'status', '--porcelain') or git(source, 'status', '--porcelain'):
        raise ValueError('The source and installed checkouts must be separate and clean.')
    if git(source, 'remote', 'get-url', 'origin') != git(installed, 'remote', 'get-url', 'origin'):
        raise ValueError('Source and installation repository identities differ.')
    old = git(installed, 'rev-parse', 'HEAD')
    target = git(source, 'rev-parse', 'HEAD')
    subprocess.run(['git', '-C', str(source), 'merge-base', '--is-ancestor', old, target], check=True)
    version = tomllib.loads((source / 'pyproject.toml').read_text())['project']['version']
    workspace = read_private_roots(source.parent / 'canvas-mcp-local' / 'upload-config.json')
    if workspace is None or 'CANVAS_IMAGE_UPLOAD_ROOT' not in workspace:
        raise ValueError('The reviewed workspace image root is not configured.')
    root = Path(workspace['CANVAS_IMAGE_UPLOAD_ROOT'])
    if root.is_symlink() or not root.is_dir() or root.stat().st_uid != os.getuid():
        raise ValueError('The image root must be an owned directory, not a symlink.')
    fallback = Path.home() / '.config' / 'canvas-mcp' / 'upload-config.json'
    existing = read_private_roots(fallback)
    values = dict(existing or {}, CANVAS_IMAGE_UPLOAD_ROOT=str(root))
    print(f'Preflight passed: fast-forward installed connector to {version}; preserve newer features and policy.')
    if not args.apply:
        print('Nothing written. Run with --apply manually only when ready to install.')
        return
    git(installed, 'fetch', str(source), target)
    if git(installed, 'rev-parse', 'HEAD') != old or git(installed, 'status', '--porcelain'):
        raise RuntimeError('Installed checkout changed during preparation; stopped.')
    git(installed, 'merge', '--ff-only', target)
    fallback.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if read_private_roots(fallback) != existing:
        raise RuntimeError('Upload configuration changed during preparation; stopped.')
    if existing is not None:
        backup = fallback.with_name('upload-config.backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(existing, f)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=fallback.parent, prefix='.upload-config-', delete=False) as f:
            temporary = Path(f.name)
            os.fchmod(f.fileno(), 0o600)
            json.dump(values, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, fallback)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    if read_private_roots(fallback) != values or git(installed, 'rev-parse', 'HEAD') != target:
        raise RuntimeError('Installation read-back verification failed.')
    environment = dict(os.environ)
    for name in ['CANVAS_IMAGE_UPLOAD_ROOT', 'CANVAS_UPLOAD_CONFIG', 'PYTHONPATH']:
        environment.pop(name, None)
    request = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                          'params': {'name': 'canvas_get_write_policy', 'arguments': {}}}) + '\n'
    run = subprocess.run([str(python), '-m', 'canvas_mcp'], cwd=tempfile.gettempdir(), env=environment,
                         input=request, text=True, capture_output=True, check=True, timeout=30)
    response = json.loads(run.stdout)
    status = json.loads(response['result']['content'][0]['text'])
    if status['connector_version'] != version or not status['image_upload_configuration']['ready']:
        raise RuntimeError('Cached-launch configuration verification failed.')
    print(f'Installed connector {version} verified with old launch arguments and no upload environment settings.')
    print('Previous installed commit: ' + old)
    print('Now fully quit and reopen ChatGPT. Active MCP verification is still required afterward.')


if __name__ == '__main__':
    main()
