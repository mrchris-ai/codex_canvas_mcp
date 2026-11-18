"""Bounded, enrollment-only SIS imports. No caller-supplied CSV or import options."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import ssl
import stat
import urllib.parse
import urllib.request

import certifi

from canvas_mcp import server as api

FIELDS = {"account_id", "course_id", "section_id", "sis_course_id", "sis_section_id", "role_id", "students"}
MAX_STUDENTS = 25
PENDING = {"initializing", "created", "importing"}
GUIDANCE = (
    "Use only for explicitly authorized students in one exact course section. Generate an "
    "enrollments-only CSV with active StudentEnrollment records, no notifications, no batch/diff "
    "mode, and no override options. Preview is read-only. Submission requires the exact temporary "
    "approved_sis_enrollment_import_fingerprints entry in private policy version 10, preview "
    "confirmation, and MCP-client approval. The user's explicit import request authorizes this "
    "temporary exact grant; preserve other settings and mode 0600, remove the grant after the "
    "attempt, and never bypass a host denial. Verify the returned import with the read-only "
    "verification tool until complete. Pending or uncertain imports are not success. Never "
    "automatically resubmit, erase a receipt, or change an invitation/inactive/completed enrollment."
)


def target(args, extra=()):
    if not isinstance(args, dict) or set(args) != FIELDS | set(extra):
        raise ValueError("Use only the documented SIS enrollment import fields.")
    result = {k: api.require_positive_id(args[k], k) for k in
              ("account_id", "course_id", "section_id", "role_id")}
    for key in ("sis_course_id", "sis_section_id"):
        value = args[key]
        if key == "sis_section_id" and value is None:
            result[key] = None
        else:
            value = api.require_text(value, key)
            if any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError("SIS identifiers cannot contain control characters.")
            result[key] = value
    students = args["students"]
    if not isinstance(students, list) or not 1 <= len(students) <= MAX_STUDENTS:
        raise ValueError("A SIS enrollment import requires 1 to 25 students.")
    result["students"] = []
    for student in students:
        if not isinstance(student, dict) or set(student) != {"canvas_user_id", "sis_user_id"}:
            raise ValueError("Each student needs exactly one Canvas ID and one SIS ID.")
        user_id = api.require_positive_id(student["canvas_user_id"], "canvas_user_id")
        sis_id = api.require_text(student["sis_user_id"], "sis_user_id")
        if any(ord(c) < 32 or ord(c) == 127 for c in sis_id):
            raise ValueError("SIS identifiers cannot contain control characters.")
        result["students"].append({"canvas_user_id": user_id, "sis_user_id": sis_id})
    if (len({s["canvas_user_id"] for s in students}) != len(students)
            or len({s["sis_user_id"] for s in students}) != len(students)):
        raise ValueError("Duplicate Canvas or SIS student IDs are not allowed.")
    result["students"].sort(key=lambda s: s["sis_user_id"])
    return result


def fingerprint(t):
    return hashlib.sha256(json.dumps({"origin": api.canvas_base_url(),
        "operation": "sis_active_student_enrollments", "target": t}, sort_keys=True).encode()).hexdigest()


def student_target(t, student):
    return {k: t[k] for k in ("course_id", "section_id", "sis_course_id", "role_id")} | student


def csv_bytes(t, students):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["course_id", "user_id", "role", "section_id", "status", "notify"])
    for s in students:
        writer.writerow([t["sis_course_id"], s["sis_user_id"], "student",
                         t["sis_section_id"] or "", "active", "false"])
    result = stream.getvalue().encode("utf-8")
    if len(result) > 65536:
        raise ValueError("Generated SIS enrollment CSV exceeds its size limit.")
    return result


def preflight(t):
    account = api.api_get(f"/api/v1/accounts/{t['account_id']}")
    if (not isinstance(account, dict) or account.get("id") != t["account_id"]
            or account.get("workflow_state") != "active" or "parent_account_id" not in account
            or account["parent_account_id"] is not None):
        raise ValueError("SIS imports require the exact active root account.")
    permissions = api.api_get(f"/api/v1/accounts/{t['account_id']}/permissions",
                              {"permissions[]": ["manage_sis"]})
    if not isinstance(permissions, dict) or permissions.get("manage_sis") is not True:
        raise PermissionError("Canvas SIS import permission is required.")
    course = api.api_get(f"/api/v1/courses/{t['course_id']}")
    if (not isinstance(course, dict) or course.get("id") != t["course_id"]
            or course.get("root_account_id") != t["account_id"]
            or course.get("sis_course_id") != t["sis_course_id"]
            or course.get("workflow_state") not in {"available", "unpublished"}):
        raise ValueError("The SIS course does not match the approved root account and course.")
    section = api.api_get(f"/api/v1/sections/{t['section_id']}")
    if (not isinstance(section, dict) or section.get("id") != t["section_id"]
            or section.get("course_id") != t["course_id"]
            or section.get("sis_section_id") != t["sis_section_id"]
            or section.get("nonxlist_course_id") not in (None, t["course_id"])):
        raise ValueError("Section SIS identity or cross-listing does not match.")
    if t["sis_section_id"] is None:
        sections = api.provisioning_list(f"/api/v1/courses/{t['course_id']}/sections", {})
        if len(sections) != 1 or sections[0].get("id") != t["section_id"]:
            raise ValueError("A section without a SIS ID must be the course's sole default section.")
    pending, existing, summaries = [], [], []
    for student in t["students"]:
        context, records = api.student_enrollment_preflight(student_target(t, student))
        # Resolve the SIS identifier separately: the CSV uses this identity, not the numeric ID.
        sis_profile = api.api_get("/api/v1/users/sis_user_id:" +
                                  urllib.parse.quote(student["sis_user_id"], safe="") + "/profile")
        if (not isinstance(sis_profile, dict) or sis_profile.get("id") != student["canvas_user_id"]
                or sis_profile.get("sis_user_id") != student["sis_user_id"]):
            raise ValueError("SIS lookup does not resolve to the exact Canvas student.")
        state = records[0].get("enrollment_state") if records else None
        if state == "invited":
            raise ValueError("An existing invitation requires separate review; SIS import will not activate it.")
        if state == "active":
            existing.append({**student, "enrollment_id": records[0]["id"], "changed": False,
                             "enrollment_state": "active"})
        else:
            pending.append(student)
        summaries.append({**student, "name": context["student_name"], "existing_state": state})
    return {"course_name": course.get("name"), "students": summaries,
            "pending": pending, "existing": existing, "csv": csv_bytes(t, pending)}


def confirmation(t, payload):
    digest = hashlib.sha256(payload).hexdigest()
    return f"APPROVE CANVAS SIS ENROLLMENT IMPORT {fingerprint(t)} {digest}"


def preview(args):
    t = target(args)
    p = preflight(t)
    return {"target": t, "course_name": p["course_name"], "students": p["students"],
            "import_row_count": len(p["pending"]), "already_active": p["existing"],
            "csv": p["csv"].decode(), "approval_fingerprint": fingerprint(t),
            "confirmation": confirmation(t, p["csv"]), "read_only": True,
            "effect": "Import only these student enrollments as active; notifications are disabled."}


def receipt_path(digest, create=False):
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid import fingerprint.")
    directory = Path.home() / ".canvas-mcp-sis-imports"
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    try:
        info = directory.lstat()
    except FileNotFoundError:
        return directory / (digest + ".json")
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise api.ConfigurationError("SIS receipt directory must be owned, non-symlink, and mode 0700.")
    return directory / (digest + ".json")


def read_receipt(digest):
    path = receipt_path(digest)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise api.ConfigurationError("Unsafe SIS import receipt.")
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise api.ConfigurationError("SIS import receipt exceeds its size limit.")
    value = json.loads(raw, object_pairs_hook=api._unique_policy_keys)
    if not isinstance(value, dict) or value.get("fingerprint") != digest:
        raise api.ConfigurationError("SIS import receipt identity mismatch.")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("SIS import redirects are forbidden. Inspect the import before retrying.")


def post_csv(account_id, payload):
    # Private transport accepts only generated bytes at the one reviewed endpoint.
    api.require_positive_id(account_id, "account_id")
    url = api.canvas_base_url() + f"/api/v1/accounts/{account_id}/sis_imports?import_type=instructure_csv"
    request = urllib.request.Request(url, data=payload, method="POST", headers={
        "Authorization": "Bearer " + api.canvas_token(), "Content-Type": "text/csv",
        "Accept": "application/json", "User-Agent": f"{api.SERVER_NAME}/{api.SERVER_VERSION}"})
    context = ssl.create_default_context(cafile=certifi.where())
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), NoRedirect())
    with opener.open(request, timeout=30) as response:
        return json.load(response)


def submit(args):
    t = target(args, ("confirmation",))
    digest = fingerprint(t)
    policy = api.write_policy()
    if not policy["enabled"] or digest not in policy.get("approved_sis_enrollment_import_fingerprints", []):
        raise PermissionError("This exact SIS enrollment import lacks local policy approval.")
    receipt = read_receipt(digest)
    if receipt is not None:
        raise RuntimeError(f"This batch already has a submission receipt (import ID: {receipt.get('import_id')}). "
                           "Use verification or inspect Canvas. Do not resubmit or erase the receipt automatically.")
    p = preflight(t)
    if args["confirmation"] != confirmation(t, p["csv"]):
        raise PermissionError("SIS import confirmation must match the current preview and generated CSV.")
    if not p["pending"]:
        return {"submitted": False, "verified": True, "students": p["existing"]}
    receipt = {"fingerprint": digest, "target": t, "submitted_students": p["pending"],
               "already_active": p["existing"], "csv_sha256": hashlib.sha256(p["csv"]).hexdigest(),
               "import_id": None}
    # Reserve before POST. A crash or timeout leaves evidence that blocks duplicate submission.
    path = receipt_path(digest, create=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    import_id = None
    with os.fdopen(fd, "w") as stream:
        json.dump(receipt, stream); stream.flush(); os.fsync(stream.fileno())
        try:
            created = post_csv(t["account_id"], p["csv"])
            import_id = api.require_positive_id(created.get("id"), "SIS import ID")
            receipt["import_id"] = import_id
            stream.seek(0); json.dump(receipt, stream); stream.truncate(); stream.flush(); os.fsync(stream.fileno())
        except Exception as exc:
            raise RuntimeError(f"SIS import submission needs inspection (import ID: {import_id}, receipt: {digest}). "
                               "No automatic retry; inspect Canvas and preserve the receipt.") from exc
    return {"submitted": True, "verified": False, "import_id": import_id,
            "import_row_count": len(p["pending"]), "approval_fingerprint": digest,
            "next_step": "Call canvas_verify_sis_enrollment_import with this target and import_id until terminal."}


def verify(args):
    t = target(args, ("import_id",))
    import_id = api.require_positive_id(args["import_id"], "import_id")
    receipt = read_receipt(fingerprint(t))
    if not receipt or receipt.get("target") != t or receipt.get("import_id") != import_id:
        raise ValueError("Import ID is not bound to this target's local submission receipt.")
    status = api.api_get(f"/api/v1/accounts/{t['account_id']}/sis_imports/{import_id}")
    if not isinstance(status, dict) or status.get("id") != import_id:
        raise ValueError("SIS import identity mismatch.")
    state = status.get("workflow_state")
    if state in PENDING:
        return {"import_id": import_id, "workflow_state": state, "verified": False, "pending": True}
    data = status.get("data") or {}
    counts = data.get("counts") or {}
    if (state != "imported" or status.get("processing_errors") or status.get("processing_warnings")
            or status.get("batch_mode") not in (None, False)
            or data.get("import_type") != "instructure_csv" or data.get("supplied_batches") != ["enrollment"]
            or counts.get("enrollments") != len(receipt["submitted_students"])
            or any(counts.get(k, 0) != 0 for k in ("error_count", "warning_count", "accounts", "terms",
                   "courses", "sections", "users", "groups", "group_memberships", "xlists"))):
        raise RuntimeError(f"SIS import {import_id} requires inspection; state or import counts/errors differ. Do not retry.")
    imported_ids = {s["canvas_user_id"] for s in receipt["submitted_students"]}
    results = []
    for student in t["students"]:
        expected = student_target(t, student)
        records = api.provisioning_list(f"/api/v1/courses/{t['course_id']}/enrollments",
            {"user_id": student["canvas_user_id"], "state[]": api.ENROLLMENT_STATES})
        live = [r for r in records if r.get("enrollment_state") == "active"]
        if (len(live) != 1 or not all(api.student_enrollment_matches(r, expected) for r in records)
                or any(r is not live[0] and r.get("enrollment_state") != "deleted" for r in records)
                or (student["canvas_user_id"] in imported_ids and live[0].get("sis_import_id") != import_id)):
            raise RuntimeError(f"SIS import {import_id} enrollment read-back failed. Inspect Canvas; do not retry.")
        results.append({**student, "enrollment_id": live[0]["id"], "enrollment_state": "active",
                        "changed": student["canvas_user_id"] in imported_ids})
    return {"import_id": import_id, "workflow_state": state, "verified": True, "pending": False,
            "students": results}


def tools():
    properties = {k: {"type": "integer", "minimum": 1} for k in
                  ("account_id", "course_id", "section_id", "role_id")}
    properties.update({"sis_course_id": {"type": "string", "minLength": 1, "maxLength": 255},
        "sis_section_id": {"type": ["string", "null"], "minLength": 1, "maxLength": 255},
        "students": {"type": "array", "minItems": 1, "maxItems": MAX_STUDENTS, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"canvas_user_id": {"type": "integer", "minimum": 1},
                           "sis_user_id": {"type": "string", "minLength": 1, "maxLength": 255}},
            "required": ["canvas_user_id", "sis_user_id"]}}})
    result = []
    for name, extra, write in (("preview", {}, False), ("submit", {"confirmation": {"type": "string"}}, True),
                              ("verify", {"import_id": {"type": "integer", "minimum": 1}}, False)):
        props = properties | extra
        result.append({"name": f"canvas_{name}_sis_enrollment_import", "description": GUIDANCE,
            "inputSchema": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False},
            "annotations": {"readOnlyHint": not write, "destructiveHint": write, "idempotentHint": not write}})
    return result
