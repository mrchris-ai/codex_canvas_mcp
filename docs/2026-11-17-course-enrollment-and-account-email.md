# Account email choice and course enrollment (0.19.0)

Policy version 10. Package version 0.19.0. Versions 1–9 remain readable. They keep the scopes they already had and do not grant `approved_course_enrollment_targets`.

## Account creation

`canvas_preview_user_creation` and `canvas_create_user_account` still create one account in the active root account. Full name, `short_name`, sortable name, ten-digit SIS ID, duplicate checks, permission checks, and read-back are unchanged.

`send_confirmation` is optional and defaults to `true`. That default matches the previous hard-coded `pseudonym[send_confirmation]`, so callers that omit it still ask Canvas to email the user. `false` sends `false`. The normalized target always includes the boolean, and the origin-bound approval fingerprint covers it. An approval for `true` does not approve `false`. Fingerprints issued before this field existed need a new preview.

The login and contact email are the same lowercased address. The domain must equal one entry from `CANVAS_USER_CREATION_EMAIL_DOMAINS`. An unset or blank value uses `sdccd.edu` and `student.sdccd.edu`. A set value replaces that list. Matching is exact after lowercasing, so `mail.sdccd.edu`, `sdccd.edu.example`, and mixed-case suffixes fail before any Canvas call. Plus-addressing remains allowed because the previous format check allowed it.

## Course enrollment

`canvas_preview_course_enrollment` is read-only. `canvas_enroll_user_in_course` is destructive and is not idempotent. The user must already exist. Pass `canvas_user_id`, `sis_user_id`, or both. When both are present they must resolve to one person. The POST uses the numeric Canvas user id from that read.

The course must be unpublished or available. An optional `section_id` must belong to that course. `enrollment_state` accepts `active`, `invited`, and `inactive`; omission stores `invited`. `notify` accepts a boolean; omission stores `false`, which is Canvas's default of not sending mail. The policy entry has to name `enrollment_state` and `notify` explicitly so a partial object is not a grant.

Roles come from `GET /api/v1/accounts/:account_id/roles?show_inherited=true` on the course account. The role id must be present, `base_role_type` must be `StudentEnrollment`, `TeacherEnrollment`, `TaEnrollment`, `DesignerEnrollment`, or `ObserverEnrollment`, and `workflow_state` must be `active` or `built_in`. Account membership roles and inactive roles stop the write. The payload sets `enrollment[type]` from the live `base_role_type` and `enrollment[role_id]` from the request. `limit_privileges_to_course_section` is false.

An identical same-role enrollment is a verified no-op. A same-role enrollment whose state does not match is left as it is. A different role on the same user does not block a new enrollment. Uncertain results are not retried.

## Policy

`canvas_enroll_user_in_course` requires all of the following:

- `enabled: true`
- the normalized target in `approved_course_enrollment_targets`
- confirmation `APPROVE CANVAS COURSE ENROLLMENT` plus the SHA-256 of that target
- MCP-client approval of the mutating call

`enabled` plus the confirmation does not authorize a missing entry. `canvas_enroll_student` and `canvas_enroll_instructor` keep their previous gates. Student invitation still has no per-student allowlist.

Example entry shape, with sample ids only:

```json
{
  "course_id": 30,
  "role_id": 4435,
  "sis_user_id": "0000000001",
  "section_id": 40,
  "enrollment_state": "invited",
  "notify": false
}
```

Omit `section_id` when Canvas should use the course's default section. `sis_user_id` stays a string. A Canvas user id may be used instead of, or together with, the SIS id.
