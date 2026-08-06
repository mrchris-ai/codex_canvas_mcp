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
SERVER_VERSION = "0.2.0"
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
    concealed_fields = []
    for field in payload.get("fields", []):
        label = field.get("label") or field.get("id")
        if label == field_name and field.get("type") == "CONCEALED" and field.get("value"):
            return str(field["value"])
        if field.get("type") == "CONCEALED" and field.get("value"):
            concealed_fields.append(field)
    if field_name == "credential" and len(concealed_fields) == 1:
        return str(concealed_fields[0]["value"])
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


def require_write_approval(course_id: int, confirmation: Any, action: str) -> None:
    policy = write_policy()
    if not policy["enabled"] or course_id not in policy["approved_course_ids"]:
        raise PermissionError(
            "Canvas writing is disabled or this course is not approved by the local policy."
        )
    expected = f"APPROVE CANVAS {action.upper()} WRITE course {course_id}"
    if confirmation != expected:
        raise PermissionError(f"Confirmation must exactly match: {expected}")


def require_text(value: Any, name: str, maximum: int = 255) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must contain 1 to {maximum} characters.")
    return value


def require_boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean.")
    return value


def content_write(course_id: int, confirmation: Any, action: str, method: str, path: str, data: dict[str, Any]) -> Any:
    require_write_approval(course_id, confirmation, action)
    return request_api(method, path, data=data)


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
    {
        "name": "canvas_create_module",
        "description": "Create exactly one Canvas module in an approved course. Disabled by default and requires explicit confirmation.",
        "inputSchema": {"type": "object", "properties": {"course_id": {"type": "integer", "minimum": 1}, "name": {"type": "string", "minLength": 1, "maxLength": 255}, "published": {"type": "boolean", "default": False}, "position": {"type": "integer", "minimum": 1}, "confirmation": {"type": "string"}}, "required": ["course_id", "name", "confirmation"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "canvas_create_module_item",
        "description": "Add one existing Canvas content item or external URL to an existing module in an approved course. Disabled by default and requires explicit confirmation.",
        "inputSchema": {"type": "object", "properties": {"course_id": {"type": "integer", "minimum": 1}, "module_id": {"type": "integer", "minimum": 1}, "type": {"type": "string", "enum": ["Page", "Assignment", "Discussion", "Quiz", "File", "ExternalUrl"]}, "title": {"type": "string", "minLength": 1, "maxLength": 255}, "content_id": {"type": "integer", "minimum": 1}, "page_url": {"type": "string", "minLength": 1}, "external_url": {"type": "string", "format": "uri"}, "new_tab": {"type": "boolean", "default": False}, "position": {"type": "integer", "minimum": 1}, "confirmation": {"type": "string"}}, "required": ["course_id", "module_id", "type", "title", "confirmation"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "canvas_create_assignment",
        "description": "Create exactly one Canvas assignment in an approved course. Disabled by default and requires explicit confirmation.",
        "inputSchema": {"type": "object", "properties": {"course_id": {"type": "integer", "minimum": 1}, "name": {"type": "string", "minLength": 1, "maxLength": 255}, "description": {"type": "string"}, "points_possible": {"type": "number", "minimum": 0}, "submission_types": {"type": "array", "items": {"type": "string"}}, "published": {"type": "boolean", "default": False}, "confirmation": {"type": "string"}}, "required": ["course_id", "name", "confirmation"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "canvas_create_discussion",
        "description": "Create exactly one Canvas discussion in an approved course. Disabled by default and requires explicit confirmation.",
        "inputSchema": {"type": "object", "properties": {"course_id": {"type": "integer", "minimum": 1}, "title": {"type": "string", "minLength": 1, "maxLength": 255}, "message": {"type": "string"}, "discussion_type": {"type": "string", "enum": ["threaded", "focused"], "default": "threaded"}, "published": {"type": "boolean", "default": False}, "confirmation": {"type": "string"}}, "required": ["course_id", "title", "message", "confirmation"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "canvas_create_classic_quiz",
        "description": "Create exactly one Canvas Classic Quiz in an approved course. New Quizzes are not supported by this tool. Disabled by default and requires explicit confirmation.",
        "inputSchema": {"type": "object", "properties": {"course_id": {"type": "integer", "minimum": 1}, "title": {"type": "string", "minLength": 1, "maxLength": 255}, "description": {"type": "string"}, "quiz_type": {"type": "string", "enum": ["practice_quiz", "assignment", "graded_survey", "survey"], "default": "practice_quiz"}, "published": {"type": "boolean", "default": False}, "confirmation": {"type": "string"}}, "required": ["course_id", "title", "confirmation"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "canvas_create_classic_quiz_question",
        "description": "Add one question to an existing Canvas Classic Quiz in an approved course. Disabled by default and requires explicit confirmation.",
        "inputSchema": {"type": "object", "properties": {"course_id": {"type": "integer", "minimum": 1}, "quiz_id": {"type": "integer", "minimum": 1}, "question_name": {"type": "string", "minLength": 1, "maxLength": 255}, "question_text": {"type": "string"}, "question_type": {"type": "string", "enum": ["multiple_choice_question", "true_false_question", "short_answer_question", "essay_question", "multiple_answers_question"]}, "points_possible": {"type": "number", "minimum": 0}, "answers": {"type": "array", "items": {"type": "object"}}, "confirmation": {"type": "string"}}, "required": ["course_id", "quiz_id", "question_name", "question_text", "question_type", "confirmation"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
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
        title = require_text(args.get("title"), "title")
        body = args.get("body")
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
        return content_write(course_id, args.get("confirmation"), "PAGE", method, path, {"wiki_page": page})
    if name == "canvas_create_module":
        course_id = require_course_id(args.get("course_id"))
        module: dict[str, Any] = {"name": require_text(args.get("name"), "name"), "published": args.get("published", False)}
        require_boolean(module["published"], "published")
        if "position" in args:
            module["position"] = require_course_id(args["position"])
        return content_write(course_id, args.get("confirmation"), "MODULE", "POST", f"/api/v1/courses/{course_id}/modules", {"module": module})
    if name == "canvas_create_module_item":
        course_id = require_course_id(args.get("course_id"))
        module_id = require_course_id(args.get("module_id"))
        item_type = args.get("type")
        if item_type not in {"Page", "Assignment", "Discussion", "Quiz", "File", "ExternalUrl"}:
            raise ValueError("type must be a supported Canvas module-item type.")
        item: dict[str, Any] = {"type": item_type, "title": require_text(args.get("title"), "title")}
        if "content_id" in args:
            item["content_id"] = require_course_id(args["content_id"])
        if "page_url" in args:
            item["page_url"] = require_text(args["page_url"], "page_url")
        if "external_url" in args:
            external_url = args["external_url"]
            parsed = urllib.parse.urlsplit(external_url) if isinstance(external_url, str) else None
            if not parsed or parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("external_url must be an HTTPS URL.")
            item["external_url"] = external_url
        if item_type == "Page" and "page_url" not in item:
            raise ValueError("Page module items require page_url.")
        if item_type == "ExternalUrl" and "external_url" not in item:
            raise ValueError("ExternalUrl module items require external_url.")
        if item_type not in {"Page", "ExternalUrl"} and "content_id" not in item:
            raise ValueError(f"{item_type} module items require content_id.")
        if "new_tab" in args:
            item["new_tab"] = require_boolean(args["new_tab"], "new_tab")
        if "position" in args:
            item["position"] = require_course_id(args["position"])
        return content_write(course_id, args.get("confirmation"), "MODULE ITEM", "POST", f"/api/v1/courses/{course_id}/modules/{module_id}/items", {"module_item": item})
    if name == "canvas_create_assignment":
        course_id = require_course_id(args.get("course_id"))
        assignment: dict[str, Any] = {"name": require_text(args.get("name"), "name"), "published": args.get("published", False)}
        require_boolean(assignment["published"], "published")
        for field in ("description", "points_possible", "submission_types"):
            if field in args:
                assignment[field] = args[field]
        if "description" in assignment and not isinstance(assignment["description"], str):
            raise ValueError("description must be a string.")
        if "points_possible" in assignment and (isinstance(assignment["points_possible"], bool) or not isinstance(assignment["points_possible"], (int, float)) or assignment["points_possible"] < 0):
            raise ValueError("points_possible must be a non-negative number.")
        if "submission_types" in assignment and (not isinstance(assignment["submission_types"], list) or not all(isinstance(value, str) for value in assignment["submission_types"])):
            raise ValueError("submission_types must be a list of strings.")
        return content_write(course_id, args.get("confirmation"), "ASSIGNMENT", "POST", f"/api/v1/courses/{course_id}/assignments", {"assignment": assignment})
    if name == "canvas_create_discussion":
        course_id = require_course_id(args.get("course_id"))
        discussion_type = args.get("discussion_type", "threaded")
        if discussion_type not in {"threaded", "focused"}:
            raise ValueError("discussion_type must be threaded or focused.")
        discussion = {"title": require_text(args.get("title"), "title"), "message": args.get("message"), "discussion_type": discussion_type, "published": args.get("published", False)}
        if not isinstance(discussion["message"], str):
            raise ValueError("message must be a string.")
        require_boolean(discussion["published"], "published")
        return content_write(course_id, args.get("confirmation"), "DISCUSSION", "POST", f"/api/v1/courses/{course_id}/discussion_topics", discussion)
    if name == "canvas_create_classic_quiz":
        course_id = require_course_id(args.get("course_id"))
        quiz_type = args.get("quiz_type", "practice_quiz")
        if quiz_type not in {"practice_quiz", "assignment", "graded_survey", "survey"}:
            raise ValueError("quiz_type is not supported.")
        quiz: dict[str, Any] = {"title": require_text(args.get("title"), "title"), "quiz_type": quiz_type, "published": args.get("published", False)}
        require_boolean(quiz["published"], "published")
        if "description" in args:
            if not isinstance(args["description"], str):
                raise ValueError("description must be a string.")
            quiz["description"] = args["description"]
        return content_write(course_id, args.get("confirmation"), "CLASSIC QUIZ", "POST", f"/api/v1/courses/{course_id}/quizzes", {"quiz": quiz})
    if name == "canvas_create_classic_quiz_question":
        course_id = require_course_id(args.get("course_id"))
        quiz_id = require_course_id(args.get("quiz_id"))
        question_type = args.get("question_type")
        allowed_question_types = {"multiple_choice_question", "true_false_question", "short_answer_question", "essay_question", "multiple_answers_question"}
        if question_type not in allowed_question_types:
            raise ValueError("question_type is not supported.")
        question: dict[str, Any] = {"question_name": require_text(args.get("question_name"), "question_name"), "question_text": args.get("question_text"), "question_type": question_type}
        if not isinstance(question["question_text"], str):
            raise ValueError("question_text must be a string.")
        if "points_possible" in args:
            points = args["points_possible"]
            if isinstance(points, bool) or not isinstance(points, (int, float)) or points < 0:
                raise ValueError("points_possible must be a non-negative number.")
            question["points_possible"] = points
        if "answers" in args:
            if not isinstance(args["answers"], list) or not all(isinstance(answer, dict) for answer in args["answers"]):
                raise ValueError("answers must be a list of objects.")
            question["answers"] = args["answers"]
        return content_write(course_id, args.get("confirmation"), "CLASSIC QUIZ QUESTION", "POST", f"/api/v1/courses/{course_id}/quizzes/{quiz_id}/questions", {"question": question})
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
