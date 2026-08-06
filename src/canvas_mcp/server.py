#!/usr/bin/env python3
"""A small stdio MCP server for narrowly scoped Canvas access.

Credentials are resolved at call time through 1Password. They are never read from
repository files, included in tool output, or logged.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

import certifi

SERVER_NAME = "codex-canvas-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-03-26"


class ConfigurationError(RuntimeError):
    """Raised when required local configuration is absent or unsafe."""


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"Required environment variable {name} is not set.")
    return value


def canvas_base_url() -> str:
    raw = required_env("CANVAS_BASE_URL").rstrip("/")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path:
        raise ConfigurationError("CANVAS_BASE_URL must be an HTTPS origin with no path.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError("CANVAS_BASE_URL must not contain credentials, a query, or a fragment.")
    return raw


def run_secret_command(args: list[str], env: dict[str, str] | None = None) -> str:
    try:
        return subprocess.check_output(
            args,
            text=True,
            env=env,
            stderr=subprocess.DEVNULL,
            timeout=20,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ConfigurationError(f"Credential helper failed: {args[0]}") from exc


def service_account_token() -> str:
    direct = os.environ.get("OP_SERVICE_ACCOUNT_TOKEN", "").strip()
    if direct:
        return direct

    if sys.platform != "darwin":
        raise ConfigurationError(
            "Set OP_SERVICE_ACCOUNT_TOKEN in the server process on non-macOS systems."
        )
    service = required_env("CANVAS_KEYCHAIN_SERVICE")
    account = required_env("CANVAS_KEYCHAIN_ACCOUNT")
    return run_secret_command(
        ["security", "find-generic-password", "-w", "-s", service, "-a", account]
    )


def canvas_token() -> str:
    env = dict(os.environ)
    env["OP_SERVICE_ACCOUNT_TOKEN"] = service_account_token()
    item = required_env("CANVAS_OP_ITEM")
    vault = required_env("CANVAS_OP_VAULT")
    field_name = os.environ.get("CANVAS_OP_FIELD", "credential").strip() or "credential"
    raw = run_secret_command(
        ["op", "item", "get", item, "--vault", vault, "--format", "json"],
        env=env,
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError("1Password returned invalid item data.") from exc
    for field in payload.get("fields", []):
        label = field.get("label") or field.get("id")
        if label == field_name and field.get("type") == "CONCEALED" and field.get("value"):
            return str(field["value"])
    raise ConfigurationError(
        f"1Password item has no concealed field named {field_name!r}."
    )


def validate_api_path(path: str) -> str:
    if not isinstance(path, str):
        raise ValueError("Canvas path must be a string.")
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("Use a path only; pass query parameters separately.")
    if not parsed.path.startswith("/api/v1/") or "//" in parsed.path:
        raise ValueError("Only normalized Canvas /api/v1/ paths are allowed.")
    return parsed.path


def request_api(
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> Any:
    if method not in {"GET", "POST", "PUT"}:
        raise ValueError("Unsupported HTTP method.")
    url = canvas_base_url() + validate_api_path(path)
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    headers = {
        "Authorization": "Bearer " + canvas_token(),
        "Accept": "application/json",
        "User-Agent": f"{SERVER_NAME}/{SERVER_VERSION}",
    }
    body = None if data is None else json.dumps(data).encode("utf-8")
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Canvas API returned HTTP {exc.code} {exc.reason}.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Canvas API connection failed.") from exc


def api_get(path: str, query: dict[str, Any] | None = None) -> Any:
    return request_api("GET", path, query=query)


def disabled_policy() -> dict[str, Any]:
    return {"version": 1, "enabled": False, "approved_course_ids": []}


def write_policy() -> dict[str, Any]:
    configured_path = os.environ.get("CANVAS_WRITE_POLICY", "").strip()
    if not configured_path:
        return disabled_policy()
    path = Path(configured_path).expanduser()
    try:
        stat = path.stat()
    except FileNotFoundError:
        return disabled_policy()
    if hasattr(os, "getuid") and stat.st_uid != os.getuid():
        raise ConfigurationError("The Canvas write policy must be owned by the current user.")
    if stat.st_mode & 0o077:
        raise ConfigurationError("The Canvas write policy must have file mode 0600.")
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError("The Canvas write policy is unreadable or invalid JSON.") from exc
    if set(policy) != {"version", "enabled", "approved_course_ids"}:
        raise ConfigurationError("The Canvas write policy contains unexpected keys.")
    if policy["version"] != 1 or not isinstance(policy["enabled"], bool):
        raise ConfigurationError("The Canvas write policy has invalid version or enabled values.")
    course_ids = policy["approved_course_ids"]
    if not isinstance(course_ids, list) or any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in course_ids
    ):
        raise ConfigurationError("approved_course_ids must contain only positive integers.")
    if len(course_ids) != len(set(course_ids)):
        raise ConfigurationError("approved_course_ids must not contain duplicates.")
    return policy


def require_course_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("course_id must be a positive integer.")
    return value


def require_write_approval(course_id: int, confirmation: Any) -> None:
    policy = write_policy()
    if not policy["enabled"] or course_id not in policy["approved_course_ids"]:
        raise PermissionError(
            "Canvas writing is disabled or this course is not approved by the local policy."
        )
    expected = f"APPROVE CANVAS PAGE WRITE course {course_id}"
    if confirmation != expected:
        raise PermissionError(f"Confirmation must exactly match: {expected}")


TOOLS: list[dict[str, Any]] = [
    {
        "name": "canvas_get_current_user",
        "description": "Read the authenticated Canvas user's profile. Read-only.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "canvas_list_modules",
        "description": "List modules and module items for one Canvas course. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {"course_id": {"type": "integer", "minimum": 1}},
            "required": ["course_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "canvas_read_api",
        "description": "Read one Canvas REST API v1 path with GET. Cannot make changes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "pattern": "^/api/v1/"},
                "query": {"type": "object"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "canvas_get_write_policy",
        "description": "Show write-policy state and approved course IDs. Does not expose credentials.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "canvas_write_page",
        "description": (
            "Create or update exactly one Canvas page. Disabled by default. Requires a local "
            "per-course allowlist, exact user confirmation, and approval of this write action "
            "in the MCP client. This is not generic Canvas write access."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer", "minimum": 1},
                "title": {"type": "string", "minLength": 1, "maxLength": 255},
                "body": {"type": "string"},
                "page_url": {
                    "type": "string",
                    "description": "Existing Canvas page URL slug. Omit to create a page.",
                },
                "published": {"type": "boolean", "default": False},
                "confirmation": {"type": "string"},
            },
            "required": ["course_id", "title", "body", "confirmation"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        },
    },
]


def call_tool(name: str, args: dict[str, Any]) -> Any:
    if name == "canvas_get_current_user":
        return api_get("/api/v1/users/self")
    if name == "canvas_list_modules":
        course_id = require_course_id(args.get("course_id"))
        return api_get(
            f"/api/v1/courses/{course_id}/modules",
            {"include[]": "items", "per_page": 100},
        )
    if name == "canvas_read_api":
        return api_get(args.get("path"), args.get("query"))
    if name == "canvas_get_write_policy":
        return write_policy()
    if name == "canvas_write_page":
        course_id = require_course_id(args.get("course_id"))
        require_write_approval(course_id, args.get("confirmation"))
        title = args.get("title")
        body = args.get("body")
        if not isinstance(title, str) or not title.strip() or len(title) > 255:
            raise ValueError("title must contain 1 to 255 characters.")
        if not isinstance(body, str):
            raise ValueError("body must be a string.")
        page = {
            "title": title,
            "body": body,
            "published": args.get("published", False),
        }
        if not isinstance(page["published"], bool):
            raise ValueError("published must be a boolean.")
        page_url = args.get("page_url")
        if page_url is not None and (not isinstance(page_url, str) or not page_url.strip()):
            raise ValueError("page_url must be a non-empty Canvas page slug.")
        path = f"/api/v1/courses/{course_id}/pages"
        method = "POST"
        if page_url:
            path += "/" + urllib.parse.quote(page_url, safe="")
            method = "PUT"
        return request_api(method, path, data={"wiki_page": page})
    raise ValueError("Unknown tool.")


def respond(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    ident = request.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": ident,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Canvas reads are available after local credential setup. Page writing is "
                    "disabled unless a secure local per-course policy enables it. Never reveal credentials."
                ),
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": ident, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = request.get("params") or {}
        result = call_tool(params.get("name"), params.get("arguments") or {})
        return {
            "jsonrpc": "2.0",
            "id": ident,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps(result, separators=(",", ":"))}
                ]
            },
        }
    if ident is None:
        return None
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "Method not found."}}


def main() -> None:
    for line in sys.stdin:
        ident: Any = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Request must be a JSON object.")
            ident = request.get("id")
            response = handle_request(request)
            if response is not None:
                respond(response)
        except Exception as exc:
            if ident is not None:
                respond(
                    {
                        "jsonrpc": "2.0",
                        "id": ident,
                        "error": {"code": -32000, "message": str(exc)},
                    }
                )


if __name__ == "__main__":
    main()
