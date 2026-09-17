#!/usr/bin/env python3
"""A small stdio MCP server for narrowly scoped Canvas access.

Credentials are resolved at call time through 1Password. They are never read from
repository files, included in tool output, or logged.
"""

from __future__ import annotations

import json
import ipaddress
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
import re
import secrets
import ssl
import subprocess
import sys
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

import certifi

SERVER_NAME = "codex-canvas-mcp"
SERVER_VERSION = "0.9.0"
PROTOCOL_VERSION = "2025-03-26"
MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024
IMAGE_CONTENT_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


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


def canvas_http_error_detail(exc: urllib.error.HTTPError) -> str:
    """Return a short Canvas-authored error without echoing arbitrary response content."""
    try:
        payload = json.loads(exc.read(8192).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    details = {
        key: payload[key]
        for key in ("message", "error", "errors")
        if key in payload
    }
    if not details:
        return ""
    rendered = json.dumps(details, ensure_ascii=True, separators=(",", ":"))
    return rendered[:1000]


def request_api(
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> Any:
    if method not in {"GET", "POST", "PUT", "DELETE"}:
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
        detail = canvas_http_error_detail(exc)
        suffix = f" Details: {detail}" if detail else ""
        raise RuntimeError(
            f"Canvas API returned HTTP {exc.code} {exc.reason}.{suffix}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Canvas API connection failed.") from exc


def api_get(path: str, query: dict[str, Any] | None = None) -> Any:
    return request_api("GET", path, query=query)


def disabled_policy() -> dict[str, Any]:
    return {
        "version": 2,
        "enabled": False,
        "approved_course_ids": [],
        "approved_rubric_delete_course_ids": [],
    }


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
    if not isinstance(policy, dict):
        raise ConfigurationError("The Canvas write policy must be a JSON object.")
    version = policy.get("version")
    expected_keys = {"version", "enabled", "approved_course_ids"}
    if version == 2:
        expected_keys.add("approved_rubric_delete_course_ids")
    if set(policy) != expected_keys:
        raise ConfigurationError("The Canvas write policy contains unexpected keys.")
    if version not in {1, 2} or not isinstance(policy["enabled"], bool):
        raise ConfigurationError("The Canvas write policy has invalid version or enabled values.")
    course_ids = policy["approved_course_ids"]
    if not isinstance(course_ids, list) or any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in course_ids
    ):
        raise ConfigurationError("approved_course_ids must contain only positive integers.")
    if len(course_ids) != len(set(course_ids)):
        raise ConfigurationError("approved_course_ids must not contain duplicates.")
    rubric_delete_course_ids = policy.get("approved_rubric_delete_course_ids", [])
    if not isinstance(rubric_delete_course_ids, list) or any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in rubric_delete_course_ids
    ):
        raise ConfigurationError(
            "approved_rubric_delete_course_ids must contain only positive integers."
        )
    if len(rubric_delete_course_ids) != len(set(rubric_delete_course_ids)):
        raise ConfigurationError(
            "approved_rubric_delete_course_ids must not contain duplicates."
        )
    return {
        "version": 2,
        "enabled": policy["enabled"],
        "approved_course_ids": course_ids,
        "approved_rubric_delete_course_ids": rubric_delete_course_ids,
    }


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


def require_assignment_update_approval(
    course_id: int,
    assignment_id: int,
    confirmation: Any,
) -> None:
    policy = write_policy()
    if not policy["enabled"] or course_id not in policy["approved_course_ids"]:
        raise PermissionError(
            "Canvas writing is disabled or this course is not approved by the local policy."
        )
    expected = (
        f"APPROVE CANVAS ASSIGNMENT UPDATE course {course_id} "
        f"assignment {assignment_id}"
    )
    if confirmation != expected:
        raise PermissionError(f"Confirmation must exactly match: {expected}")


def require_announcement_update_approval(
    course_id: int,
    announcement_id: int,
    confirmation: Any,
) -> None:
    policy = write_policy()
    if not policy["enabled"] or course_id not in policy["approved_course_ids"]:
        raise PermissionError(
            "Canvas writing is disabled or this course is not approved by the local policy."
        )
    expected = (
        f"APPROVE CANVAS ANNOUNCEMENT UPDATE course {course_id} "
        f"announcement {announcement_id}"
    )
    if confirmation != expected:
        raise PermissionError(f"Confirmation must exactly match: {expected}")


def require_delete_approval(course_id: int, confirmation: Any, resource: str) -> None:
    policy = write_policy()
    if not policy["enabled"] or course_id not in policy["approved_course_ids"]:
        raise PermissionError(
            "Canvas writing is disabled or this course is not approved by the local policy."
        )
    expected = f"APPROVE CANVAS {resource.upper()} DELETE course {course_id}"
    if confirmation != expected:
        raise PermissionError(f"Confirmation must exactly match: {expected}")


def require_rubric_delete_approval(
    course_id: int,
    rubric_id: int,
    confirmation: Any,
) -> None:
    policy = write_policy()
    if (
        not policy["enabled"]
        or course_id not in policy.get("approved_rubric_delete_course_ids", [])
    ):
        raise PermissionError(
            "Canvas rubric deletion is disabled or this course is not approved by the "
            "operation-specific local policy."
        )
    expected = f"APPROVE CANVAS RUBRIC DELETE course {course_id} rubric {rubric_id}"
    if confirmation != expected:
        raise PermissionError(f"Confirmation must exactly match: {expected}")


def require_image_upload_approval(course_id: int, confirmation: Any) -> None:
    policy = write_policy()
    if not policy["enabled"] or course_id not in policy["approved_course_ids"]:
        raise PermissionError(
            "Canvas writing is disabled or this course is not approved by the local policy."
        )
    expected = f"APPROVE CANVAS IMAGE UPLOAD course {course_id}"
    if confirmation != expected:
        raise PermissionError(f"Confirmation must exactly match: {expected}")


def require_text(value: Any, name: str, maximum: int = 255) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must contain 1 to {maximum} characters.")
    return value


def require_positive_id(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def require_boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean.")
    return value


def require_iso8601_datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be an ISO 8601 datetime.")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO 8601 datetime.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone.")
    return parsed


def format_iso8601_datetime(value: datetime) -> str:
    rendered = value.isoformat(timespec="seconds")
    if rendered.endswith("+00:00"):
        return rendered[:-6] + "Z"
    return rendered


def require_number(value: Any, name: str, *, minimum: float = 0) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < minimum
    ):
        raise ValueError(f"{name} must be a number greater than or equal to {minimum}.")
    return float(value)


def image_upload_root() -> Path:
    configured = required_env("CANVAS_IMAGE_UPLOAD_ROOT")
    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise ConfigurationError("CANVAS_IMAGE_UPLOAD_ROOT must be an absolute path.")
    if path.is_symlink():
        raise ConfigurationError("CANVAS_IMAGE_UPLOAD_ROOT must not be a symbolic link.")
    try:
        resolved = path.resolve(strict=True)
        stat = resolved.stat()
    except OSError as exc:
        raise ConfigurationError("CANVAS_IMAGE_UPLOAD_ROOT is unavailable.") from exc
    if not resolved.is_dir():
        raise ConfigurationError("CANVAS_IMAGE_UPLOAD_ROOT must be a directory.")
    if hasattr(os, "getuid") and stat.st_uid != os.getuid():
        raise ConfigurationError("CANVAS_IMAGE_UPLOAD_ROOT must be owned by the current user.")
    return resolved


def validate_image_file(value: Any) -> tuple[Path, str, int]:
    file_path = Path(require_text(value, "file_path", 4096)).expanduser()
    if not file_path.is_absolute():
        raise ValueError("file_path must be an absolute path.")
    if file_path.is_symlink():
        raise ValueError("file_path must not be a symbolic link.")
    try:
        resolved = file_path.resolve(strict=True)
        stat = resolved.stat()
    except OSError as exc:
        raise ValueError("file_path must identify an available image file.") from exc
    root = image_upload_root()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError("file_path must be inside CANVAS_IMAGE_UPLOAD_ROOT.") from exc
    if not resolved.is_file():
        raise ValueError("file_path must identify a regular file.")
    if hasattr(os, "getuid") and stat.st_uid != os.getuid():
        raise PermissionError("The image file must be owned by the current user.")
    if stat.st_size <= 0 or stat.st_size > MAX_IMAGE_UPLOAD_BYTES:
        raise ValueError("The image must be between 1 byte and 10 MiB.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", resolved.name):
        raise ValueError("The image filename may contain only letters, numbers, dot, underscore, and hyphen.")
    content_type = IMAGE_CONTENT_TYPES.get(resolved.suffix.lower())
    if content_type is None:
        raise ValueError("Only PNG, JPEG, and WebP images may be uploaded.")
    with resolved.open("rb") as handle:
        header = handle.read(12)
    signatures_match = {
        "image/jpeg": header.startswith(b"\xff\xd8\xff"),
        "image/png": header.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": header.startswith(b"RIFF") and header[8:12] == b"WEBP",
    }
    if not signatures_match[content_type]:
        raise ValueError("The image contents do not match the filename extension.")
    return resolved, content_type, stat.st_size


def validate_upload_url(value: Any) -> str:
    if not isinstance(value, str):
        raise RuntimeError("Canvas returned an invalid image upload URL.")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise RuntimeError("Canvas returned an unsafe image upload URL.")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise RuntimeError("Canvas returned an unsafe image upload host.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise RuntimeError("Canvas returned an unsafe image upload host.")
    return value


def multipart_image_body(
    upload_params: Any,
    image_path: Path,
    content_type: str,
) -> tuple[bytes, str]:
    if not isinstance(upload_params, dict) or not upload_params:
        raise RuntimeError("Canvas returned no image upload parameters.")
    boundary = "----codex-canvas-" + secrets.token_hex(16)
    body = bytearray()
    for raw_name, raw_value in upload_params.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise RuntimeError("Canvas returned invalid image upload parameters.")
        if any(character in raw_name for character in ('\r', '\n', '"')):
            raise RuntimeError("Canvas returned an unsafe image upload parameter name.")
        body.extend(f"--{boundary}\r\n".encode("ascii"))
        body.extend(
            f'Content-Disposition: form-data; name="{raw_name}"\r\n\r\n'.encode("utf-8")
        )
        body.extend(raw_value.encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode("ascii"))
    body.extend(
        (
            f'Content-Disposition: form-data; name="file"; filename="{image_path.name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(image_path.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode("ascii"))
    return bytes(body), boundary


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def post_image_payload(
    upload_url: Any,
    upload_params: Any,
    image_path: Path,
    content_type: str,
) -> tuple[int, str | None]:
    url = validate_upload_url(upload_url)
    body, boundary = multipart_image_body(upload_params, image_path, content_type)
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": f"{SERVER_NAME}/{SERVER_VERSION}",
        },
    )
    context = ssl.create_default_context(cafile=certifi.where())
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(request, timeout=60) as response:
            return response.status, response.headers.get("Location")
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            return exc.code, exc.headers.get("Location")
        raise RuntimeError(f"Canvas image storage returned HTTP {exc.code} {exc.reason}.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Canvas image storage connection failed.") from exc


def request_canvas_location(method: str, location: Any) -> Any:
    if not isinstance(location, str):
        raise RuntimeError("Canvas image upload did not provide a completion location.")
    parsed = urllib.parse.urlsplit(location)
    configured = urllib.parse.urlsplit(canvas_base_url())
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != configured.scheme or parsed.netloc.lower() != configured.netloc.lower():
            raise RuntimeError("Canvas image upload returned a completion location outside Canvas.")
    if parsed.username or parsed.password or parsed.fragment:
        raise RuntimeError("Canvas image upload returned an unsafe completion location.")
    path = validate_api_path(parsed.path)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True) or None
    return request_api(method, path, query=query)


def upload_canvas_image(args: dict[str, Any]) -> dict[str, Any]:
    course_id = require_course_id(args.get("course_id"))
    require_image_upload_approval(course_id, args.get("confirmation"))
    image_path, content_type, size = validate_image_file(args.get("file_path"))
    initial = request_api(
        "POST",
        f"/api/v1/courses/{course_id}/files",
        data={
            "name": image_path.name,
            "size": size,
            "content_type": content_type,
            "on_duplicate": "rename",
        },
    )
    if not isinstance(initial, dict):
        raise RuntimeError("Canvas returned an invalid image upload response.")
    status, location = post_image_payload(
        initial.get("upload_url"),
        initial.get("upload_params"),
        image_path,
        content_type,
    )
    if 300 <= status < 400:
        created = request_canvas_location("POST", location)
    elif status == 201:
        created = request_canvas_location("GET", location)
    else:
        raise RuntimeError(f"Canvas image storage returned unexpected HTTP {status}.")
    if not isinstance(created, dict):
        raise RuntimeError("Canvas returned an invalid completed image upload.")
    file_id = require_course_id(created.get("id"))
    verified = api_get(f"/api/v1/courses/{course_id}/files/{file_id}")
    if not isinstance(verified, dict):
        raise RuntimeError("Canvas returned invalid image verification data.")
    verified_type = verified.get("content-type") or verified.get("content_type")
    if verified_type != content_type or verified.get("size") != size:
        raise RuntimeError("Canvas image verification did not match the local image.")
    return {
        "id": file_id,
        "display_name": verified.get("display_name"),
        "filename": verified.get("filename"),
        "content_type": verified_type,
        "size": verified.get("size"),
        "folder_id": verified.get("folder_id"),
        "canvas_path": f"/courses/{course_id}/files/{file_id}/preview",
    }


def parse_assignment_url(value: Any) -> tuple[int, int | None, int | None]:
    assignment_url = require_text(value, "assignment_url", 2048)
    parsed = urllib.parse.urlsplit(assignment_url)
    configured = urllib.parse.urlsplit(canvas_base_url())
    if parsed.username or parsed.password:
        raise ValueError("assignment_url must not contain credentials.")
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != configured.scheme or parsed.netloc.lower() != configured.netloc.lower():
            raise ValueError("assignment_url must use the configured Canvas origin.")
    elif not parsed.path.startswith("/"):
        raise ValueError("assignment_url must be an absolute Canvas URL or root-relative path.")

    assignment_match = re.fullmatch(
        r"/courses/([1-9][0-9]*)/assignments/([1-9][0-9]*)(?:/.*)?",
        parsed.path.rstrip("/"),
    )
    if assignment_match:
        course_id, assignment_id = (int(value) for value in assignment_match.groups())
        query_assignment_ids = urllib.parse.parse_qs(parsed.query).get("assignment_id", [])
        if query_assignment_ids and any(value != str(assignment_id) for value in query_assignment_ids):
            raise ValueError("assignment_url contains conflicting assignment IDs.")
        return course_id, assignment_id, None

    module_item_match = re.fullmatch(
        r"/courses/([1-9][0-9]*)/modules/items/([1-9][0-9]*)(?:/.*)?",
        parsed.path.rstrip("/"),
    )
    if module_item_match:
        course_id, module_item_id = (int(value) for value in module_item_match.groups())
        return course_id, None, module_item_id

    course_match = re.match(r"^/courses/([1-9][0-9]*)(?:/|$)", parsed.path)
    query_assignment_ids = urllib.parse.parse_qs(parsed.query).get("assignment_id", [])
    if course_match and len(query_assignment_ids) == 1 and query_assignment_ids[0].isdigit() and int(query_assignment_ids[0]) > 0:
        return int(course_match.group(1)), int(query_assignment_ids[0]), None
    raise ValueError(
        "assignment_url must identify a Canvas assignment directly, through a module item, "
        "or with an assignment_id query parameter."
    )


def rubric_payload(criteria: Any) -> tuple[dict[str, Any], float]:
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 50:
        raise ValueError("criteria must contain between 1 and 50 rubric criteria.")
    normalized: dict[str, Any] = {}
    total_points = 0.0
    for criterion_index, criterion in enumerate(criteria):
        if not isinstance(criterion, dict):
            raise ValueError("Each rubric criterion must be an object.")
        if set(criterion) != {"description", "long_description", "points", "ratings"}:
            raise ValueError("Each rubric criterion must contain only description, long_description, points, and ratings.")
        points = require_number(criterion["points"], f"criteria[{criterion_index}].points")
        ratings = criterion["ratings"]
        if not isinstance(ratings, list) or not 2 <= len(ratings) <= 20:
            raise ValueError("Each rubric criterion must contain between 2 and 20 ratings.")
        normalized_ratings: dict[str, Any] = {}
        seen_rating_points: set[float] = set()
        ordered_ratings: list[tuple[str, str, float]] = []
        for rating_index, rating in enumerate(ratings):
            if not isinstance(rating, dict) or set(rating) != {"description", "long_description", "points"}:
                raise ValueError("Each rubric rating must contain only description, long_description, and points.")
            rating_points = require_number(
                rating["points"],
                f"criteria[{criterion_index}].ratings[{rating_index}].points",
            )
            if rating_points > points:
                raise ValueError("A rubric rating cannot exceed its criterion points.")
            if rating_points in seen_rating_points:
                raise ValueError("Rating point values must be unique within each criterion.")
            seen_rating_points.add(rating_points)
            ordered_ratings.append(
                (
                    require_text(rating["description"], "rating description"),
                    require_text(rating["long_description"], "rating long_description", 2000),
                    rating_points,
                )
            )
        for rating_index, (description, long_description, rating_points) in enumerate(
            sorted(ordered_ratings, key=lambda item: item[2], reverse=True)
        ):
            normalized_ratings[str(rating_index)] = {
                "description": description,
                "long_description": long_description,
                "points": rating_points,
            }
        normalized[str(criterion_index)] = {
            "description": require_text(criterion["description"], "criterion description"),
            "long_description": require_text(criterion["long_description"], "criterion long_description", 5000),
            "points": points,
            "criterion_use_range": False,
            "ratings": normalized_ratings,
        }
        total_points += points
    return normalized, total_points


def create_assignment_rubric(args: dict[str, Any]) -> Any:
    course_id, assignment_id, module_item_id = parse_assignment_url(args.get("assignment_url"))
    require_write_approval(course_id, args.get("confirmation"), "RUBRIC")
    if module_item_id is not None:
        module_item = api_get(f"/api/v1/courses/{course_id}/modules/items/{module_item_id}")
        if module_item.get("type") != "Assignment":
            raise ValueError("The Canvas module-item URL does not identify an assignment.")
        assignment_id = require_course_id(module_item.get("content_id"))
    if assignment_id is None:
        raise ValueError("Could not resolve an assignment from assignment_url.")

    assignment = api_get(
        f"/api/v1/courses/{course_id}/assignments/{assignment_id}",
        {"include[]": ["rubric", "rubric_settings"]},
    )
    if assignment.get("rubric_settings") or assignment.get("rubric"):
        raise ValueError("The target assignment already has a rubric; this tool will not replace it.")

    use_for_grading = args.get("use_for_grading", True)
    require_boolean(use_for_grading, "use_for_grading")
    criteria, total_points = rubric_payload(args.get("criteria"))
    assignment_points = assignment.get("points_possible")
    if use_for_grading and isinstance(assignment_points, (int, float)) and not isinstance(assignment_points, bool):
        if abs(float(assignment_points) - total_points) > 0.000001:
            raise ValueError("A grading rubric's total points must match the assignment points.")

    payload = {
        "rubric": {
            "title": require_text(args.get("title"), "title"),
            "free_form_criterion_comments": False,
            "criteria": criteria,
        },
        "rubric_association": {
            "association_id": assignment_id,
            "association_type": "Assignment",
            "use_for_grading": use_for_grading,
            "hide_score_total": False,
            "purpose": "grading",
        },
    }
    return request_api("POST", f"/api/v1/courses/{course_id}/rubrics", data=payload)


def find_course_rubric(
    course_id: int,
    rubric_id: int,
    *,
    required: bool = True,
) -> dict[str, Any] | None:
    """Find one active course rubric without relying on Canvas's fragile show route."""
    for page in range(1, 11):
        rubrics = api_get(
            f"/api/v1/courses/{course_id}/rubrics",
            {"per_page": 100, "page": page},
        )
        if not isinstance(rubrics, list):
            raise RuntimeError("Canvas returned an invalid course-rubric list.")
        for rubric in rubrics:
            if isinstance(rubric, dict) and rubric.get("id") == rubric_id:
                return rubric
        if len(rubrics) < 100:
            break
    if required:
        raise ValueError(
            f"Rubric {rubric_id} was not found among the active rubrics in course {course_id}."
        )
    return None


def inspect_course_rubric(course_id: int, rubric_id: int) -> dict[str, Any]:
    rubric = find_course_rubric(course_id, rubric_id)
    assert rubric is not None
    used_locations = api_get(
        f"/api/v1/courses/{course_id}/rubrics/{rubric_id}/used_locations"
    )
    if not isinstance(used_locations, list):
        raise RuntimeError("Canvas returned invalid rubric usage-location data.")

    blockers: list[str] = []
    if rubric.get("context_type") != "Course" or rubric.get("context_id") != course_id:
        blockers.append("The rubric is not owned by the requested course.")
    if rubric.get("read_only") is True:
        blockers.append("Canvas reports that this rubric is read-only.")
    if used_locations:
        blockers.append(
            "The rubric is still used by one or more Canvas courses or assignments."
        )

    return {
        "course_id": course_id,
        "rubric_id": rubric_id,
        "title": rubric.get("title"),
        "points_possible": rubric.get("points_possible"),
        "criteria_count": len(rubric.get("data") or []),
        "read_only": rubric.get("read_only"),
        "used_locations": used_locations,
        "deletion_ready": not blockers,
        "deletion_blockers": blockers,
        "backup": rubric,
    }


def delete_course_rubric(args: dict[str, Any]) -> dict[str, Any]:
    course_id = require_course_id(args.get("course_id"))
    rubric_id = require_positive_id(args.get("rubric_id"), "rubric_id")
    expected_title = require_text(args.get("expected_title"), "expected_title")
    require_rubric_delete_approval(course_id, rubric_id, args.get("confirmation"))

    snapshot = inspect_course_rubric(course_id, rubric_id)
    if snapshot["title"] != expected_title:
        raise ValueError(
            f"Rubric title mismatch: expected {expected_title!r}, Canvas returned {snapshot['title']!r}."
        )
    if not snapshot["deletion_ready"]:
        raise PermissionError(
            "Rubric deletion preflight failed: " + " ".join(snapshot["deletion_blockers"])
        )

    response = request_api(
        "DELETE",
        f"/api/v1/courses/{course_id}/rubrics/{rubric_id}",
    )
    if find_course_rubric(course_id, rubric_id, required=False) is not None:
        raise RuntimeError("Canvas reported success, but the rubric remains in the active course list.")
    return {
        "deleted": True,
        "course_id": course_id,
        "rubric_id": rubric_id,
        "title": expected_title,
        "used_locations_before_delete": snapshot["used_locations"],
        "backup": snapshot["backup"],
        "canvas_response": response,
    }


def content_write(course_id: int, confirmation: Any, action: str, method: str, path: str, data: dict[str, Any]) -> Any:
    require_write_approval(course_id, confirmation, action)
    return request_api(method, path, data=data)


def normalize_canvas_assignment_description(value: str) -> str:
    """Remove only link attributes Canvas predictably strips or injects."""
    normalized = re.sub(r'\s+rel="noopener"', "", value)
    normalized = re.sub(r'\s+data-api-endpoint="[^"]*"', "", normalized)
    normalized = re.sub(r'\s+data-api-returntype="[^"]*"', "", normalized)
    return normalized.strip()


def create_announcement(args: dict[str, Any]) -> dict[str, Any]:
    """Create one immediate or scheduled Canvas announcement and verify it live."""
    course_id = require_course_id(args.get("course_id"))
    confirmation = args.get("confirmation")
    require_write_approval(course_id, confirmation, "ANNOUNCEMENT")

    title = require_text(args.get("title"), "title")
    message = args.get("message")
    if not isinstance(message, str):
        raise ValueError("message must be a string.")
    discussion_type = args.get("discussion_type", "threaded")
    if discussion_type not in {"threaded", "focused"}:
        raise ValueError("discussion_type must be threaded or focused.")
    published = args.get("published", True)
    allow_participant_comments = args.get("allow_participant_comments", True)
    require_boolean(published, "published")
    require_boolean(allow_participant_comments, "allow_participant_comments")

    delayed_post_at = None
    delayed_post_at_text = None
    if "delayed_post_at" in args:
        delayed_post_at = require_iso8601_datetime(
            args.get("delayed_post_at"), "delayed_post_at"
        )
        delayed_post_at_text = format_iso8601_datetime(delayed_post_at)

    lock_at = None
    lock_at_text = None
    if "lock_at" in args:
        lock_at = require_iso8601_datetime(args.get("lock_at"), "lock_at")
        lock_at_text = format_iso8601_datetime(lock_at)

    if delayed_post_at is not None and lock_at is not None and lock_at <= delayed_post_at:
        raise ValueError("lock_at must be later than delayed_post_at.")

    payload: dict[str, Any] = {
        "title": title,
        "message": message,
        "discussion_type": discussion_type,
        "published": published,
        "is_announcement": True,
        "lock_comment": not allow_participant_comments,
    }
    if delayed_post_at_text is not None:
        payload["delayed_post_at"] = delayed_post_at_text
    if lock_at_text is not None:
        payload["lock_at"] = lock_at_text

    collection_path = f"/api/v1/courses/{course_id}/discussion_topics"
    created = request_api("POST", collection_path, data=payload)
    if not isinstance(created, dict):
        raise RuntimeError("Canvas returned an unexpected announcement creation response.")
    announcement_id = require_positive_id(created.get("id"), "announcement_id")
    item_path = f"{collection_path}/{announcement_id}"
    verified = api_get(item_path)
    if not isinstance(verified, dict) or verified.get("id") != announcement_id:
        raise RuntimeError(
            f"Canvas returned an unexpected record while verifying announcement {announcement_id}."
        )

    failures: list[str] = []
    if verified.get("is_announcement") is not True:
        failures.append("is_announcement")
    if verified.get("title") != title:
        failures.append("title")
    if verified.get("message") != message:
        failures.append("message")
    if verified.get("published") is not published:
        failures.append("published")
    if delayed_post_at is not None:
        saved_delayed_post_at = require_iso8601_datetime(
            verified.get("delayed_post_at"), "delayed_post_at"
        )
        if saved_delayed_post_at != delayed_post_at:
            failures.append("delayed_post_at")
    if lock_at is not None:
        saved_lock_at = require_iso8601_datetime(verified.get("lock_at"), "lock_at")
        if saved_lock_at != lock_at:
            failures.append("lock_at")
    expected_comments_disabled = not allow_participant_comments
    if verified.get("comments_disabled") is not expected_comments_disabled:
        failures.append("comments_disabled")
    if failures:
        raise RuntimeError(
            f"Canvas announcement {announcement_id} failed verification for: "
            + ", ".join(failures)
            + "."
        )

    return {
        "created": True,
        "verified": True,
        "course_id": course_id,
        "announcement_id": announcement_id,
        "title": verified.get("title"),
        "html_url": verified.get("html_url"),
        "published": verified.get("published"),
        "delayed_post_at": verified.get("delayed_post_at"),
        "lock_at": verified.get("lock_at"),
        "comments_disabled": verified.get("comments_disabled"),
    }


def set_announcement_three_day_window(args: dict[str, Any]) -> dict[str, Any]:
    course_id = require_course_id(args.get("course_id"))
    announcement_id = require_positive_id(args.get("announcement_id"), "announcement_id")
    confirmation = args.get("confirmation")
    require_write_approval(course_id, confirmation, "ANNOUNCEMENT WINDOW")
    path = f"/api/v1/courses/{course_id}/discussion_topics/{announcement_id}"
    announcement = api_get(path)
    if not isinstance(announcement, dict) or announcement.get("id") != announcement_id:
        raise RuntimeError("Canvas returned an unexpected announcement record.")
    if announcement.get("is_announcement") is not True:
        raise ValueError("The requested discussion topic is not an announcement.")
    posted_at = require_iso8601_datetime(announcement.get("posted_at"), "posted_at")
    display_until = posted_at + timedelta(days=3)
    display_until_text = format_iso8601_datetime(display_until)
    request_api("PUT", path, data={"lock_at": display_until_text})
    verified = api_get(path)
    if not isinstance(verified, dict) or verified.get("id") != announcement_id:
        raise RuntimeError("Canvas returned an unexpected announcement during verification.")
    saved_lock_at = require_iso8601_datetime(verified.get("lock_at"), "lock_at")
    if saved_lock_at != display_until:
        raise RuntimeError("Canvas did not save the expected three-day display-until value.")
    return {
        "course_id": course_id,
        "announcement_id": announcement_id,
        "title": verified.get("title"),
        "posted_at": announcement.get("posted_at"),
        "previous_lock_at": announcement.get("lock_at"),
        "lock_at": verified.get("lock_at"),
        "verified": True,
    }


def update_announcement_message(args: dict[str, Any]) -> dict[str, Any]:
    """Update only one exact Canvas announcement body and verify protected settings."""
    course_id = require_course_id(args.get("course_id"))
    announcement_id = require_positive_id(args.get("announcement_id"), "announcement_id")
    expected_title = require_text(args.get("expected_title"), "expected_title")
    message = args.get("message")
    if not isinstance(message, str):
        raise ValueError("message must be a string.")
    require_announcement_update_approval(
        course_id,
        announcement_id,
        args.get("confirmation"),
    )

    path = f"/api/v1/courses/{course_id}/discussion_topics/{announcement_id}"
    before = api_get(path)
    if not isinstance(before, dict) or before.get("id") != announcement_id:
        raise RuntimeError("Canvas returned an unexpected announcement record.")
    if before.get("is_announcement") is not True:
        raise ValueError("The requested discussion topic is not an announcement.")
    if before.get("title") != expected_title:
        raise ValueError(
            f"Announcement title mismatch: expected {expected_title!r}, "
            f"Canvas returned {before.get('title')!r}."
        )

    preserved_fields = (
        "title",
        "is_announcement",
        "published",
        "discussion_type",
        "delayed_post_at",
        "posted_at",
        "lock_at",
        "comments_disabled",
        "is_section_specific",
        "group_category_id",
        "pinned",
    )
    preserved_before = {
        field: before[field] for field in preserved_fields if field in before
    }

    request_api("PUT", path, data={"message": message})
    after = api_get(path)
    if not isinstance(after, dict) or after.get("id") != announcement_id:
        raise RuntimeError("Canvas returned an unexpected announcement during verification.")
    if after.get("message") != message:
        raise RuntimeError("Canvas did not save the expected announcement message.")

    changed_fields = {
        field: {"before": preserved_before[field], "after": after.get(field)}
        for field in preserved_before
        if after.get(field) != preserved_before[field]
    }
    if changed_fields:
        changed_names = ", ".join(sorted(changed_fields))
        raise RuntimeError(
            "Canvas changed protected announcement settings unexpectedly: " + changed_names
        )

    return {
        "course_id": course_id,
        "announcement_id": announcement_id,
        "title": expected_title,
        "html_url": after.get("html_url"),
        "message_length": len(message),
        "preserved_settings": preserved_before,
        "verified": True,
    }


def update_assignment_description(args: dict[str, Any]) -> dict[str, Any]:
    course_id = require_course_id(args.get("course_id"))
    assignment_id = require_positive_id(args.get("assignment_id"), "assignment_id")
    expected_name = require_text(args.get("expected_name"), "expected_name")
    description = args.get("description")
    if not isinstance(description, str):
        raise ValueError("description must be a string.")
    require_assignment_update_approval(
        course_id,
        assignment_id,
        args.get("confirmation"),
    )

    path = f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
    before = api_get(path)
    if not isinstance(before, dict) or before.get("id") != assignment_id:
        raise RuntimeError("Canvas returned an unexpected assignment record.")
    if before.get("name") != expected_name:
        raise ValueError(
            f"Assignment name mismatch: expected {expected_name!r}, "
            f"Canvas returned {before.get('name')!r}."
        )

    preserved_fields = (
        "name",
        "points_possible",
        "submission_types",
        "published",
        "workflow_state",
        "due_at",
        "lock_at",
        "unlock_at",
        "assignment_group_id",
    )
    preserved_before = {
        field: before[field] for field in preserved_fields if field in before
    }

    request_api("PUT", path, data={"assignment": {"description": description}})
    after = api_get(path)
    if not isinstance(after, dict) or after.get("id") != assignment_id:
        raise RuntimeError("Canvas returned an unexpected assignment during verification.")
    if after.get("name") != expected_name:
        raise RuntimeError("Canvas changed the assignment name unexpectedly.")
    saved_description = after.get("description")
    if not isinstance(saved_description, str) or (
        normalize_canvas_assignment_description(saved_description)
        != normalize_canvas_assignment_description(description)
    ):
        raise RuntimeError("Canvas did not save the expected assignment description.")

    changed_fields = {
        field: {"before": preserved_before[field], "after": after.get(field)}
        for field in preserved_before
        if after.get(field) != preserved_before[field]
    }
    if changed_fields:
        changed_names = ", ".join(sorted(changed_fields))
        raise RuntimeError(
            "Canvas changed protected assignment settings unexpectedly: " + changed_names
        )

    return {
        "course_id": course_id,
        "assignment_id": assignment_id,
        "name": expected_name,
        "description_length": len(description),
        "preserved_settings": preserved_before,
        "verified": True,
    }


def content_delete(course_id: int, confirmation: Any, resource: str, path: str) -> Any:
    require_delete_approval(course_id, confirmation, resource)
    return request_api("DELETE", path)


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
        "name": "canvas_inspect_rubric",
        "description": (
            "Inspect one exact course-owned rubric, its complete definition, and every Canvas "
            "course or assignment where it is used. Reports whether zero-dependency deletion "
            "preflight passes. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer", "minimum": 1},
                "rubric_id": {"type": "integer", "minimum": 1},
            },
            "required": ["course_id", "rubric_id"],
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
        "name": "canvas_upload_image",
        "description": (
            "Upload exactly one locally verified PNG, JPEG, or WebP image to an approved Canvas "
            "course. The file must be inside CANVAS_IMAGE_UPLOAD_ROOT and no larger than 10 MiB. "
            "Disabled by default and requires exact image-upload confirmation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer", "minimum": 1},
                "file_path": {"type": "string", "minLength": 1, "maxLength": 4096},
                "confirmation": {"type": "string"},
            },
            "required": ["course_id", "file_path", "confirmation"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        },
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
        "name": "canvas_update_assignment",
        "description": (
            "Update only the description of one existing Canvas assignment after verifying its "
            "exact ID and name. Reads the assignment back and verifies that protected settings "
            "did not change. Disabled by default and requires target-specific confirmation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer", "minimum": 1},
                "assignment_id": {"type": "integer", "minimum": 1},
                "expected_name": {"type": "string", "minLength": 1, "maxLength": 255},
                "description": {"type": "string"},
                "confirmation": {"type": "string"},
            },
            "required": [
                "course_id",
                "assignment_id",
                "expected_name",
                "description",
                "confirmation",
            ],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
        },
    },
    {
        "name": "canvas_create_assignment_rubric",
        "description": (
            "Create and attach exactly one rubric to an assignment in an approved course. "
            "Accepts a same-origin Canvas assignment URL, assignment module-item URL, or course URL "
            "containing assignment_id. Query strings and fragments are supported. Refuses to replace "
            "an existing rubric. Disabled by default and requires explicit confirmation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "assignment_url": {"type": "string", "minLength": 1, "maxLength": 2048},
                "title": {"type": "string", "minLength": 1, "maxLength": 255},
                "criteria": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string", "minLength": 1, "maxLength": 255},
                            "long_description": {"type": "string", "minLength": 1, "maxLength": 5000},
                            "points": {"type": "number", "minimum": 0},
                            "ratings": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 20,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "description": {"type": "string", "minLength": 1, "maxLength": 255},
                                        "long_description": {"type": "string", "minLength": 1, "maxLength": 2000},
                                        "points": {"type": "number", "minimum": 0},
                                    },
                                    "required": ["description", "long_description", "points"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["description", "long_description", "points", "ratings"],
                        "additionalProperties": False,
                    },
                },
                "use_for_grading": {"type": "boolean", "default": True},
                "confirmation": {"type": "string"},
            },
            "required": ["assignment_url", "title", "criteria", "confirmation"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "canvas_delete_rubric",
        "description": (
            "Delete exactly one course-owned rubric only after live preflight confirms the exact "
            "title, course ownership, editable state, and zero Canvas usage locations. Requires "
            "the exact course and rubric IDs in the confirmation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer", "minimum": 1},
                "rubric_id": {"type": "integer", "minimum": 1},
                "expected_title": {"type": "string", "minLength": 1, "maxLength": 255},
                "confirmation": {"type": "string"},
            },
            "required": ["course_id", "rubric_id", "expected_title", "confirmation"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "canvas_create_discussion",
        "description": "Create exactly one Canvas discussion in an approved course. Disabled by default and requires explicit confirmation.",
        "inputSchema": {"type": "object", "properties": {"course_id": {"type": "integer", "minimum": 1}, "title": {"type": "string", "minLength": 1, "maxLength": 255}, "message": {"type": "string"}, "discussion_type": {"type": "string", "enum": ["threaded", "focused"], "default": "threaded"}, "published": {"type": "boolean", "default": False}, "confirmation": {"type": "string"}}, "required": ["course_id", "title", "message", "confirmation"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "canvas_create_announcement",
        "description": (
            "Create exactly one immediate or scheduled Canvas announcement in an approved course. "
            "Supports an exact posting time, an exact display-until time, and participant-comment "
            "control, then reads the saved announcement back and verifies every requested field. "
            "Disabled by default and requires explicit confirmation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer", "minimum": 1},
                "title": {"type": "string", "minLength": 1, "maxLength": 255},
                "message": {"type": "string"},
                "discussion_type": {
                    "type": "string",
                    "enum": ["threaded", "focused"],
                    "default": "threaded",
                },
                "published": {"type": "boolean", "default": True},
                "delayed_post_at": {"type": "string", "format": "date-time"},
                "lock_at": {"type": "string", "format": "date-time"},
                "allow_participant_comments": {"type": "boolean", "default": True},
                "confirmation": {"type": "string"},
            },
            "required": ["course_id", "title", "message", "confirmation"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        },
    },
    {
        "name": "canvas_set_announcement_three_day_window",
        "description": (
            "Set one existing Canvas announcement's display-until value to exactly 72 hours "
            "after its live posted_at timestamp. Verifies the target is an announcement and "
            "reads the saved value back. Disabled by default and requires explicit confirmation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer", "minimum": 1},
                "announcement_id": {"type": "integer", "minimum": 1},
                "confirmation": {"type": "string"},
            },
            "required": ["course_id", "announcement_id", "confirmation"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
        },
    },
    {
        "name": "canvas_update_announcement",
        "description": (
            "Update only the message body of one existing Canvas announcement after verifying "
            "its exact ID, title, and announcement type. Reads the announcement back and verifies "
            "that protected settings did not change. Disabled by default and requires "
            "target-specific confirmation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer", "minimum": 1},
                "announcement_id": {"type": "integer", "minimum": 1},
                "expected_title": {"type": "string", "minLength": 1, "maxLength": 255},
                "message": {"type": "string"},
                "confirmation": {"type": "string"},
            },
            "required": [
                "course_id",
                "announcement_id",
                "expected_title",
                "message",
                "confirmation",
            ],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
        },
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
    {
        "name": "canvas_delete_page",
        "description": "Delete exactly one Canvas page in an approved course. Requires the exact page slug and explicit confirmation.",
        "inputSchema": {"type": "object", "properties": {"course_id": {"type": "integer", "minimum": 1}, "page_url": {"type": "string", "minLength": 1}, "confirmation": {"type": "string"}}, "required": ["course_id", "page_url", "confirmation"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "canvas_delete_assignment",
        "description": "Delete exactly one Canvas assignment in an approved course. Requires the exact assignment ID and explicit confirmation.",
        "inputSchema": {"type": "object", "properties": {"course_id": {"type": "integer", "minimum": 1}, "assignment_id": {"type": "integer", "minimum": 1}, "confirmation": {"type": "string"}}, "required": ["course_id", "assignment_id", "confirmation"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "canvas_delete_discussion",
        "description": "Delete exactly one Canvas discussion in an approved course. Requires the exact discussion ID and explicit confirmation.",
        "inputSchema": {"type": "object", "properties": {"course_id": {"type": "integer", "minimum": 1}, "discussion_id": {"type": "integer", "minimum": 1}, "confirmation": {"type": "string"}}, "required": ["course_id", "discussion_id", "confirmation"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "canvas_delete_classic_quiz",
        "description": "Delete exactly one Canvas Classic Quiz in an approved course. Requires the exact quiz ID and explicit confirmation.",
        "inputSchema": {"type": "object", "properties": {"course_id": {"type": "integer", "minimum": 1}, "quiz_id": {"type": "integer", "minimum": 1}, "confirmation": {"type": "string"}}, "required": ["course_id", "quiz_id", "confirmation"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "canvas_delete_module",
        "description": "Delete exactly one Canvas module and its module-item placements in an approved course. Underlying course content is not deleted automatically.",
        "inputSchema": {"type": "object", "properties": {"course_id": {"type": "integer", "minimum": 1}, "module_id": {"type": "integer", "minimum": 1}, "confirmation": {"type": "string"}}, "required": ["course_id", "module_id", "confirmation"], "additionalProperties": False},
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
    if name == "canvas_inspect_rubric":
        course_id = require_course_id(args.get("course_id"))
        rubric_id = require_positive_id(args.get("rubric_id"), "rubric_id")
        return inspect_course_rubric(course_id, rubric_id)
    if name == "canvas_get_write_policy":
        return write_policy()
    if name == "canvas_upload_image":
        return upload_canvas_image(args)
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
    if name == "canvas_update_assignment":
        return update_assignment_description(args)
    if name == "canvas_create_assignment_rubric":
        return create_assignment_rubric(args)
    if name == "canvas_delete_rubric":
        return delete_course_rubric(args)
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
    if name == "canvas_create_announcement":
        return create_announcement(args)
    if name == "canvas_set_announcement_three_day_window":
        return set_announcement_three_day_window(args)
    if name == "canvas_update_announcement":
        return update_announcement_message(args)
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
    if name == "canvas_delete_page":
        course_id = require_course_id(args.get("course_id"))
        page_url = require_text(args.get("page_url"), "page_url")
        return content_delete(course_id, args.get("confirmation"), "PAGE", f"/api/v1/courses/{course_id}/pages/{urllib.parse.quote(page_url, safe='')}")
    if name == "canvas_delete_assignment":
        course_id = require_course_id(args.get("course_id"))
        assignment_id = require_course_id(args.get("assignment_id"))
        return content_delete(course_id, args.get("confirmation"), "ASSIGNMENT", f"/api/v1/courses/{course_id}/assignments/{assignment_id}")
    if name == "canvas_delete_discussion":
        course_id = require_course_id(args.get("course_id"))
        discussion_id = require_course_id(args.get("discussion_id"))
        return content_delete(course_id, args.get("confirmation"), "DISCUSSION", f"/api/v1/courses/{course_id}/discussion_topics/{discussion_id}")
    if name == "canvas_delete_classic_quiz":
        course_id = require_course_id(args.get("course_id"))
        quiz_id = require_course_id(args.get("quiz_id"))
        return content_delete(course_id, args.get("confirmation"), "CLASSIC QUIZ", f"/api/v1/courses/{course_id}/quizzes/{quiz_id}")
    if name == "canvas_delete_module":
        course_id = require_course_id(args.get("course_id"))
        module_id = require_course_id(args.get("module_id"))
        return content_delete(course_id, args.get("confirmation"), "MODULE", f"/api/v1/courses/{course_id}/modules/{module_id}")
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
                    "Canvas reads are available after local credential setup. Content writing and "
                    "image upload are disabled unless secure local policy and payload gates enable them. "
                    "Never reveal credentials."
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
