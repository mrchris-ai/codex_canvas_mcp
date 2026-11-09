"""Explicit, private connection metadata for clients that omit stdio env values."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit


CONNECTION_KEYS = frozenset({
    "CANVAS_BASE_URL", "CANVAS_OP_VAULT", "CANVAS_OP_ITEM", "CANVAS_OP_FIELD",
    "CANVAS_KEYCHAIN_SERVICE", "CANVAS_KEYCHAIN_ACCOUNT",
})
MAX_CONFIG_BYTES = 16 * 1024


class ConnectionConfigError(ValueError):
    """A connection file is unsafe or conflicts with the process environment."""


def _origin(value: str) -> str:
    value = value.rstrip("/")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ConnectionConfigError("Connection configuration requires an HTTPS origin.") from exc
    if (parsed.scheme != "https" or not hostname or parsed.path
            or any(char.isspace() for char in value)
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ConnectionConfigError("Connection configuration requires an HTTPS origin.")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConnectionConfigError("Connection configuration has duplicate keys.")
        result[key] = value
    return result


def load_connection_config(path: str) -> None:
    """Fill missing connection metadata only; never load tokens or write grants."""
    if not Path(path).is_absolute():
        raise ConnectionConfigError("Connection configuration path must be absolute.")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "getuid"):
        raise ConnectionConfigError("Private connection files are unsupported on this platform.")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise ConnectionConfigError(
                    "Connection configuration must be an owned regular file with mode 0600."
                )
            raw = stream.read(MAX_CONFIG_BYTES + 1)
    except OSError as exc:
        raise ConnectionConfigError("Cannot securely open connection configuration.") from exc
    if len(raw) > MAX_CONFIG_BYTES:
        raise ConnectionConfigError("Connection configuration exceeds its size limit.")
    try:
        values = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ConnectionConfigError("Connection configuration must be valid JSON.") from exc
    if not isinstance(values, dict) or not values or set(values) - CONNECTION_KEYS:
        raise ConnectionConfigError("Connection configuration contains unsupported fields.")
    if "CANVAS_BASE_URL" not in values:
        raise ConnectionConfigError("Connection configuration requires CANVAS_BASE_URL.")
    prepared = {}
    for key, value in values.items():
        if (not isinstance(value, str) or not value.strip()
                or any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
            raise ConnectionConfigError("Connection configuration values must be nonempty strings without control characters.")
        value = value.strip()
        existing = os.environ.get(key, "").strip()
        if key == "CANVAS_BASE_URL":
            value = _origin(value)
            existing = _origin(existing) if existing else ""
        if existing and existing != value:
            raise ConnectionConfigError("Connection configuration conflicts with the process environment.")
        if not existing:
            prepared[key] = value
    # Validate the whole file before changing any process configuration.
    os.environ.update(prepared)
