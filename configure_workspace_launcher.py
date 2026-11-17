"""Prepare, or explicitly apply, the Canvas workspace launcher settings.

Run without --apply to validate the proposed change without writing anything.
Run manually with --apply when the authoring session cannot write the saved
client configuration. Never use this script to bypass a host write denial.
"""

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import tomllib


def prepare(path, checkout):
    original = path.read_text()
    parsed = tomllib.loads(original)
    canvas = parsed['mcp_servers']['canvas']
    launcher = checkout / 'run_canvas_mcp.py'
    upload_config = checkout.parent / 'canvas-mcp-local' / 'upload-config.json'
    root = json.loads(upload_config.read_text())['CANVAS_IMAGE_UPLOAD_ROOT']
    if not launcher.is_file() or not Path(root).is_dir():
        raise ValueError('Workspace launcher or upload directory is missing.')
    old_args = canvas['args']
    if old_args[:2] == ['-m', 'canvas_mcp']:
        new_args = [str(launcher)] + old_args[2:]
    elif old_args[:1] == [str(launcher)]:
        new_args = old_args.copy()
    else:
        raise ValueError('Unexpected Canvas launcher; no changes made.')
    if '--upload-config' in new_args:
        new_args[new_args.index('--upload-config') + 1] = str(upload_config)
    else:
        new_args += ['--upload-config', str(upload_config)]
    expected = copy.deepcopy(parsed)
    expected['mcp_servers']['canvas']['args'] = new_args
    expected['mcp_servers']['canvas']['env']['CANVAS_IMAGE_UPLOAD_ROOT'] = root
    section, counts, lines = '', {'args': 0, 'root': 0}, []
    for line in original.splitlines(keepends=True):
        if line.strip().startswith('['):
            section = line.strip()
        if section == '[mcp_servers.canvas]' and re.match(r'^\s*args\s*=', line):
            line = 'args = ' + json.dumps(new_args) + '\n'
            counts['args'] += 1
        elif section == '[mcp_servers.canvas.env]' and re.match(r'^\s*CANVAS_IMAGE_UPLOAD_ROOT\s*=', line):
            line = 'CANVAS_IMAGE_UPLOAD_ROOT = ' + json.dumps(root) + '\n'
            counts['root'] += 1
        lines.append(line)
    candidate = ''.join(lines)
    if counts != {'args': 1, 'root': 1} or tomllib.loads(candidate) != expected:
        raise ValueError('Could not isolate the two Canvas settings; no changes made.')
    return original, candidate, expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    path = Path.home() / '.codex' / 'config.toml'
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a regular client configuration file.')
    original, candidate, expected = prepare(path, Path(__file__).resolve().parent)
    if not args.apply:
        print('Validated: only Canvas arguments and image upload root would change. Nothing written.')
        return
    if original == candidate:
        print('Canvas settings already verified. Restart ChatGPT now.')
        return
    backup = path.with_name('config.toml.canvas-backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.canvas-config-', delete=False) as f:
            temporary = Path(f.name)
            os.fchmod(f.fileno(), stat.S_IMODE(path.stat().st_mode))
            f.write(candidate)
            f.flush()
            os.fsync(f.fileno())
        if path.read_text() != original:
            raise RuntimeError('Configuration changed during preparation; no settings replaced.')
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(original)
        os.replace(temporary, path)
        temporary = None
        if tomllib.loads(path.read_text()) != expected:
            raise RuntimeError('Read-back verification failed. Restore the private backup before restarting.')
        print('Canvas settings saved and verified. Private backup: ' + str(backup))
        print('Restart ChatGPT now.')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
