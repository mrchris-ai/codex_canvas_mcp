# Exact student reactivation contract

This administrative operation restores access to one explicitly authorized inactive student enrollment. It is separate from content authoring, deactivation, regular invitations, and SIS imports.

## Target and preview

The caller supplies `course_id`, `enrollment_id`, `section_id`, `canvas_user_id`, `sis_user_id`, `sis_course_id`, and `role_id`. SIS identifiers remain strings. The read-only preview resolves and checks the course, section, student identity, built-in StudentEnrollment role, and exact existing enrollment. It returns the current state, the proposed effect, an origin-bound exact-target approval fingerprint, and a technical confirmation string.

## Authorization and write

Private policy version 11 has a dedicated `approved_student_reactivation_fingerprints` list, empty by default. Content or deactivation course grants do not authorize reactivation. Earlier supported policies normalize with an empty reactivation scope. Human authorization, local policy, and MCP-client approval are all required. The task's explicit authorization includes its temporary exact grant; preserve unrelated grants and enabled state, maintain mode 0600, and remove the request-added grant after the attempt.

The mutation rechecks the target and performs only `PUT /api/v1/courses/:course_id/enrollments/:enrollment_id/reactivate`. It accepts only the documented target fields plus the preview confirmation. It cannot delete an enrollment or account, create an enrollment, change a role, accept an invitation, or restore a completed/deleted enrollment. An exact already-active enrollment returns a verified no-op.

## Verification and uncertainty

An independent GET must show the exact enrollment active, with its identity, course, section, role, and protected settings preserved. A transport failure or unsuccessful read-back is an uncertain outcome, not permission to repeat the write. Inspect the exact current enrollment before deciding any recovery action. Another administrator or institutional synchronization may change enrollment state between checks; do not assume the preflight is an atomic lock.

## Validation

Tests mock credential and HTTP boundaries. They cover exact target validation, preview-only behavior, policy isolation, confirmation, legacy migration, active no-op, rejected states/roles/identities, the single reactivation PUT, independent read-back, and uncertain writes without retries. No production identifiers or student records belong in fixtures or version control.

API reference: [Canvas Enrollments API](https://developerdocs.instructure.com/services/canvas/resources/enrollments#method.enrollments_api.reactivate).
