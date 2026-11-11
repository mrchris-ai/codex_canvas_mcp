# Exact student enrollment deletion contract

This administrative operation deletes one explicitly authorized course enrollment. It never deletes or purges the student account. For a rollback, reconcile the exact enrollment ID against the original write receipts first; a current enrollment does not establish that the task created it. Do not include pre-existing enrollments or requests that were held without changes.

## Target and authorization

`canvas_preview_student_enrollment_deletion` requires `course_id`, `enrollment_id`, `section_id`, `canvas_user_id`, `sis_user_id`, `sis_course_id`, and `role_id`. Preserve SIS identifiers as strings, including leading zeros. It verifies the available course and SIS identifier, section and cross-listing, both Canvas-ID and SIS-ID profile lookups, the built-in student role, and exactly one enrollment for that student in that course. Extra enrollments or mismatched identity fields block the operation.

The preview returns the current state, protected fields, an origin-bound exact-target fingerprint, and a technical confirmation. Private policy version 12 adds `approved_student_enrollment_deletion_fingerprints`, empty by default. Versions 1 through 11 remain readable and confer no deletion permission. Existing reactivation grants remain separate and intact. Content, deactivation, reactivation, invitation, and SIS import permissions never authorize deletion.

After explicit human authorization of the exact deletion, add only its preview fingerprint to the private policy, preserve unrelated permissions, enabled state and mode 0600, and supply the preview confirmation to `canvas_delete_student_enrollment` under MCP-client approval. The authorized task includes this temporary setup without repeated conversational approval. Remove request-added grants after the attempt. Installation alone grants no live permission.

## Write and verification

The tool rechecks the exact target, permits only active or invited enrollments, and sends one fixed `DELETE /api/v1/courses/:course_id/enrollments/:enrollment_id` with `task=delete`. It accepts no caller-supplied endpoint, task, state, or arbitrary payload. An exact already-deleted enrollment is a verified no-op. Inactive, completed, and other states are refused. No student-account deletion, purge, broad removal, role change, or SIS import is available through this tool.

Independent reads must find that exact enrollment in `deleted` state while preserving its identity, section, role, SIS provenance, protected settings, and the student profile. A missing or altered profile prevents a success result. A write response alone never establishes completion. Transport failures, missing enrollment records, or failed read-back are uncertain outcomes: inspect with read-only tools before any operator-directed recovery and never automatically retry. Preflight is not an atomic lock against institutional synchronization or other administrators.

## Validation

Tests mock credential and network boundaries and cover state transitions, exact target and origin binding, policy migration and isolation, confirmation, no-op behavior, conflicting enrollments, protected-field read-back, account preservation, and no retries. Production identifiers and receipts must remain outside Git.

API reference: [Canvas enrollment deletion endpoint](https://developerdocs.instructure.com/services/canvas/resources/enrollments#method.enrollments_api.destroy).
