# Scheduled announcement authoring and maintenance

## Contract

`canvas_create_announcement` creates exactly one Canvas announcement in a course on the general content allowlist. It supports immediate publication or an exact `delayed_post_at`, an optional exact `lock_at`, and participant-comment control.

The operation requires all standard write gates:

- the local policy must be enabled;
- the course ID must be present in `approved_course_ids`;
- confirmation must exactly equal `APPROVE CANVAS ANNOUNCEMENT WRITE course <course_id>`; and
- the MCP client must approve the mutating tool call.

The tool posts only to `/api/v1/courses/<course_id>/discussion_topics`, forces `is_announcement` to true, and reads the new record back by ID. It verifies the title, body, announcement type, publication state, requested timestamps, and participant-comment setting before reporting success.

This is a resource-specific content-authoring operation. It does not add a generic Canvas write API and does not broaden course administration, enrollment, cross-listing, role, or settings access.

## Body-only maintenance

`canvas_update_announcement` updates only the `message` field of one existing announcement. It requires the exact course ID, announcement ID, expected title, and confirmation `APPROVE CANVAS ANNOUNCEMENT UPDATE course <course_id> announcement <announcement_id>`.

Before writing, the tool reads the live record and verifies its ID, title, and announcement type. After writing, it reads the record again, verifies the requested body, and confirms that protected settings did not change. Protected settings include the title, announcement type, publication state, discussion type, posting and display-until timestamps, comment setting, section scope, group category, and pin state.

## Exact section audience

`canvas_set_announcement_sections` changes only the section audience of one existing announcement. It requires the exact course ID, announcement ID, expected title, a non-empty unique list of section IDs, and confirmation `APPROVE CANVAS ANNOUNCEMENT SECTION WRITE course <course_id> announcement <announcement_id>`.

Before writing, the tool verifies the announcement identity and type, then resolves every requested section and confirms that it belongs to the approved course. It sends the requested IDs through Canvas's documented comma-separated `specific_sections` parameter. The post-write read includes Canvas section associations and must match the requested ID set exactly. The body, title, publication state, discussion type, posting and display-until timestamps, comment setting, group category, and pin state are protected from change.

## Validation

The unit suite covers the exact create, body-update, and section-update payloads and endpoints; allowlist and target-specific confirmation paths; timestamp ordering; announcement and section identity preflight; exact section read-back; protected-setting checks; and mutating tool annotations. The package is also compiled after the tests.
