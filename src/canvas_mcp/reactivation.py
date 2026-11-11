"""Exact inactive student enrollment reactivation, separate from other write grants."""

from __future__ import annotations

import hashlib
import json
import urllib.parse

from canvas_mcp import server as api


FIELDS = {"course_id", "enrollment_id", "canvas_user_id", "section_id",
          "sis_user_id", "sis_course_id", "role_id"}
GUIDANCE = (
    "Reactivate only an explicitly authorized, exact inactive StudentEnrollment. "
    "Preview verifies student/SIS, course/SIS, section, role, and enrollment identity. "
    "The mutation requires an enabled private policy version 11, the exact temporary "
    "approved_student_reactivation_fingerprints entry, preview confirmation, and MCP-client approval. "
    "The human's explicit reactivation request authorizes that temporary exact grant; "
    "preserve unrelated permissions and mode 0600, remove the request-added grant after the attempt, "
    "and do not ask for duplicate conversational approval or require the human to type confirmation. "
    "An identical active enrollment is a verified no-op. Invited, deleted, completed, or conflicting "
    "enrollments are refused. No creation, deletion, role changes, or SIS import is supported. "
    "Never automatically retry an uncertain write; inspect the exact enrollment through read-only "
    "Canvas tools first. Never bypass a host denial."
)
ENROLLMENT_PROTECTED = (
    "id", "course_id", "course_section_id", "user_id", "sis_user_id", "sis_course_id",
    "root_account_id", "role_id", "role", "type", "associated_user_id",
    "limit_privileges_to_course_section", "start_at", "end_at", "created_at",
    "sis_import_id", "sis_section_id", "sis_account_id", "course_integration_id",
    "section_integration_id",
)


def target(args, apply=False):
    if not isinstance(args, dict) or set(args) != FIELDS | ({"confirmation"} if apply else set()):
        raise ValueError("Student reactivation requires only the exact documented target fields.")
    result = {k: api.require_positive_id(args[k], k) for k in
              ("course_id", "enrollment_id", "canvas_user_id", "section_id", "role_id")}
    for key in ("sis_user_id", "sis_course_id"):
        value = api.require_text(args[key], key)
        if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("SIS identifiers cannot contain control characters or outer whitespace.")
        result[key] = value
    return result


def fingerprint(t):
    payload = {"origin": api.canvas_base_url(), "operation": "reactivate_student_enrollment", "target": t}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def confirmation(t):
    return "APPROVE CANVAS STUDENT REACTIVATION " + fingerprint(t)


def snapshot(value, fields):
    return {key: value.get(key) for key in fields}


def preflight(t):
    course = api.api_get(f"/api/v1/courses/{t['course_id']}")
    if (not isinstance(course, dict) or course.get("id") != t["course_id"]
            or course.get("sis_course_id") != t["sis_course_id"]
            or course.get("workflow_state") not in {"available", "unpublished"}):
        raise ValueError("The exact SIS course is not available for reactivation.")
    account_id = api.require_positive_id(course.get("account_id"), "account_id")
    root_id = api.require_positive_id(course.get("root_account_id"), "root_account_id")
    section = api.api_get(f"/api/v1/sections/{t['section_id']}")
    if (not isinstance(section, dict) or section.get("id") != t["section_id"]
            or section.get("course_id") != t["course_id"]
            or section.get("nonxlist_course_id") not in (None, t["course_id"])
            or (section.get("sis_course_id") is not None
                and section["sis_course_id"] != t["sis_course_id"])):
        raise ValueError("The section identity, SIS course, or cross-listing does not match.")
    profile = api.api_get(f"/api/v1/users/{t['canvas_user_id']}/profile")
    sis_profile = api.api_get("/api/v1/users/sis_user_id:" +
                              urllib.parse.quote(t["sis_user_id"], safe="") + "/profile")
    for user in (profile, sis_profile):
        if (not isinstance(user, dict) or user.get("id") != t["canvas_user_id"]
                or user.get("sis_user_id") != t["sis_user_id"]):
            raise ValueError("The Canvas and SIS student identities do not match.")
    roles = api.provisioning_list(f"/api/v1/accounts/{account_id}/roles", {"show_inherited": True})
    matches = [r for r in roles if r.get("id") == t["role_id"]]
    if len(matches) != 1:
        raise ValueError("The exact built-in student role was not found.")
    role = matches[0]
    if (role.get("role") != "StudentEnrollment" or role.get("label") != "Student"
            or role.get("base_role_type") != "StudentEnrollment"
            or role.get("workflow_state") not in {"active", "built_in"}):
        raise ValueError("Only the exact active built-in StudentEnrollment role is supported.")
    records = api.provisioning_list(f"/api/v1/courses/{t['course_id']}/enrollments",
        {"user_id": t["canvas_user_id"], "state[]": api.ENROLLMENT_STATES})
    if len(records) != 1:
        raise ValueError("Expected exactly one enrollment for this student and course; inspect conflicts.")
    enrollment = records[0]
    expected = {"id": t["enrollment_id"], "course_id": t["course_id"],
                "course_section_id": t["section_id"], "user_id": t["canvas_user_id"],
                "sis_user_id": t["sis_user_id"], "sis_course_id": t["sis_course_id"],
                "role_id": t["role_id"], "role": "StudentEnrollment", "type": "StudentEnrollment",
                "root_account_id": root_id}
    if any(enrollment.get(key) != value for key, value in expected.items()):
        raise ValueError("The exact enrollment, student, SIS, course, section, or role does not match.")
    if enrollment.get("enrollment_state") not in {"inactive", "active"}:
        raise ValueError("Only an inactive enrollment can be reactivated; an identical active enrollment is a no-op.")
    protected = {
        "enrollment": snapshot(enrollment, ENROLLMENT_PROTECTED),
        "course": snapshot(course, ("id", "account_id", "root_account_id", "sis_course_id")),
        "section": snapshot(section, ("id", "course_id", "sis_course_id", "sis_section_id", "nonxlist_course_id")),
        "profile": snapshot(profile, ("id", "sis_user_id", "name", "short_name", "sortable_name",
                                       "login_id", "primary_email")),
        "role": snapshot(role, ("id", "role", "label", "base_role_type", "workflow_state")),
    }
    return {"course_name": course.get("name"), "student_name": profile.get("name"),
            "current_state": enrollment["enrollment_state"], "protected": protected}


def preview(args):
    t = target(args)
    p = preflight(t)
    return {"target": t, "course_name": p["course_name"], "student_name": p["student_name"],
            "current_state": p["current_state"], "can_reactivate": p["current_state"] == "inactive",
            "protected": p["protected"], "approval_fingerprint": fingerprint(t),
            "confirmation": confirmation(t), "read_only": True,
            "effect": "Restore the exact inactive student enrollment to active; an identical active enrollment is unchanged."}


def reactivate(args):
    t = target(args, apply=True)
    policy = api.write_policy()
    if (not policy["enabled"] or fingerprint(t) not in
            policy.get("approved_student_reactivation_fingerprints", [])):
        raise PermissionError("This exact student reactivation lacks local policy approval.")
    if args["confirmation"] != confirmation(t):
        raise PermissionError("Student reactivation confirmation must exactly match the preview.")
    before = preflight(t)
    result = {"target": t, "enrollment_id": t["enrollment_id"], "enrollment_state": "active",
              "course_name": before["course_name"], "student_name": before["student_name"]}
    if before["current_state"] == "active":
        return {**result, "changed": False, "verified": True}
    try:
        # One fixed transition only. Never accept caller-supplied paths, states, or payloads.
        api.request_api("PUT", f"/api/v1/courses/{t['course_id']}/enrollments/{t['enrollment_id']}/reactivate")
        after = preflight(t)
        if after["current_state"] != "active" or after["protected"] != before["protected"]:
            raise ValueError("Reactivation state or protected fields did not match independent read-back.")
    except Exception as exc:
        raise RuntimeError(
            f"Reactivation outcome needs inspection for course {t['course_id']}, enrollment {t['enrollment_id']}. "
            "Canvas may have changed the enrollment. Do not automatically retry; read the exact enrollment "
            "and protected fields before deciding the next action."
        ) from exc
    return {**result, "changed": True, "verified": True, "preserved": after["protected"]}


def tools():
    props = {k: {"type": "integer", "minimum": 1} for k in
             ("course_id", "enrollment_id", "canvas_user_id", "section_id", "role_id")}
    props.update({k: {"type": "string", "minLength": 1, "maxLength": 255}
                  for k in ("sis_user_id", "sis_course_id")})
    result = []
    for name, write in (("canvas_preview_student_reactivation", False),
                        ("canvas_reactivate_student_enrollment", True)):
        properties = props | ({"confirmation": {"type": "string"}} if write else {})
        result.append({"name": name, "description": GUIDANCE,
            "inputSchema": {"type": "object", "properties": properties,
                            "required": list(properties), "additionalProperties": False},
            "annotations": {"readOnlyHint": not write, "destructiveHint": write, "idempotentHint": not write}})
    return result
