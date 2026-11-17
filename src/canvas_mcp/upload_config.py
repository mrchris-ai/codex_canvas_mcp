"""Private upload-root metadata for MCP hosts that omit environment values."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat


UPLOAD_KEYS = frozenset({"CANVAS_IMAGE_UPLOAD_ROOT", "CANVAS_POWERPOINT_UPLOAD_ROOT"})
MAX_UPLOAD_CONFIG_BYTES = 16 * 1024


class UploadConfigError(ValueError):
    """Upload configuration is absent or unsafe."""


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    values = {}
    for key, value in pairs:
        if key in values:
            raise UploadConfigError("Upload configuration contains duplicate keys.")
        values[key] = value
    return values


def upload_root_setting(name: str) -> str:
    """Resolve one root without granting writes or mutating the environment.

    Explicit root environment settings win. Otherwise read a separately
    provisioned private file on each call, so file edits need no restart.
    """
    if name not in UPLOAD_KEYS:
        raise UploadConfigError("Unsupported upload root setting.")
    existing = os.environ.get(name, "").strip()
    if existing:
        return existing
    configured = os.environ.get("CANVAS_UPLOAD_CONFIG", "").strip()
    path = (Path(configured).expanduser() if configured else
            Path.home() / ".config" / "canvas-mcp" / "upload-config.json")
    if not path.is_absolute():
        raise UploadConfigError("Upload configuration path must be absolute.")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "getuid"):
        raise UploadConfigError("Private upload configuration is unsupported on this platform.")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise UploadConfigError("Upload configuration must be an owned regular file with mode 0600.")
            raw = stream.read(MAX_UPLOAD_CONFIG_BYTES + 1)
    except FileNotFoundError as exc:
        raise UploadConfigError(
            f"{name} is not set and its upload configuration file is missing. "
            "Configure the root environment setting or a private upload-config.json."
        ) from exc
    except OSError as exc:
        raise UploadConfigError("Cannot securely open upload configuration.") from exc
    if len(raw) > MAX_UPLOAD_CONFIG_BYTES:
        raise UploadConfigError("Upload configuration exceeds its size limit.")
    try:
        values = json.loads(raw, object_pairs_hook=_unique_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise UploadConfigError("Upload configuration must be valid JSON.") from exc
    if not isinstance(values, dict) or set(values) - UPLOAD_KEYS:
        raise UploadConfigError("Upload configuration contains unsupported fields.")
    for value in values.values():
        if (not isinstance(value, str) or not value.strip()
                or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)
                or not Path(value.strip()).expanduser().is_absolute()):
            raise UploadConfigError("Upload roots must be absolute paths without control characters.")
    if name not in values:
        raise UploadConfigError(f"Upload configuration does not define {name}.")
    return values[name].strip()
