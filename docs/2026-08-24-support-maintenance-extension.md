# Support and maintenance extension: guarded rubric removal

Date: 2026-08-24

## Purpose

Version 0.6.0 adds the first support-maintenance workflow without adding generic Canvas mutation access. It addresses orphaned or duplicated course rubrics that cannot be removed through the instructor interface while preserving the server's read-first and disabled-by-default posture.

## Added tools

### `canvas_inspect_rubric`

This read-only tool scans active course rubrics for one exact rubric ID, reads Canvas's `used_locations` endpoint, returns the complete rubric definition as backup evidence, and reports deletion blockers. It scans up to ten 100-record pages and does not rely on Canvas's single-rubric route, which can fail for malformed or orphaned rubric records.

### `canvas_delete_rubric`

This destructive tool uses only `DELETE /api/v1/courses/<course_id>/rubrics/<rubric_id>`. It requires all of the following:

- enabled private local policy and an exact `approved_rubric_delete_course_ids` match, separate from the general content-write allowlist;
- exact course ID, rubric ID, and expected title;
- exact confirmation `APPROVE CANVAS RUBRIC DELETE course <course_id> rubric <rubric_id>`;
- client-side approval of the mutating MCP call;
- live course ownership and non-read-only state;
- an empty Canvas `used_locations` result;
- post-delete verification that the rubric has left the active course list.

The result includes the complete pre-deletion rubric definition. The tool refuses account-owned rubrics, read-only rubrics, title mismatches, active usage locations, missing rubrics, and any post-delete state mismatch.

Policy version 2 keeps `approved_course_ids` for existing content tools and adds `approved_rubric_delete_course_ids` only for this maintenance action. A course appearing only in the rubric-deletion list cannot be modified by the page, assignment, module, discussion, quiz, or image tools.

## Support-oriented diagnostics

Canvas HTTP errors now include only the structured `message`, `error`, or `errors` fields returned by Canvas, capped at 1,000 characters. Arbitrary response bodies are not echoed. This preserves actionable failure evidence without exposing headers, credentials, or unrelated response content.

## Deliberate limits

This extension does not delete rubric associations, assessments, or account-level rubrics. It does not archive or restore rubrics. It does not add enrollment, provisioning, cross-listing, user-administration, course-shell creation, or arbitrary-method tools. Those areas require separate operation-specific policies and undo designs.

## Validation

Unit tests cover the course-and-rubric-specific confirmation, read-only inspection, full backup return, title and usage preflight, scoped endpoint, post-delete active-list verification, refusal of live dependencies, and accurate tool annotations. Tests mock Canvas and credential boundaries.
