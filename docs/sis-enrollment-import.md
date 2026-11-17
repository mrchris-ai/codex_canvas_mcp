# Enrollment-only SIS import contract

Regular enrollment invitations remain unchanged. This feature supports an explicitly requested traditional SIS import that creates active student enrollments in one verified course section.

## Reviewed scope

The caller supplies the root account ID, Canvas course and section IDs, SIS course and section identifiers, the built-in StudentEnrollment role ID, and at most 25 exact Canvas/SIS student pairs. The preview displays matched names and generated CSV. SIS IDs remain strings. Student and role identities are resolved using Canvas reads. Cross-listed sections are refused; a section with no SIS ID is allowed only when it is the sole section of the exact course. Existing active enrollments are no-ops; an exact deleted enrollment can be restored. Other enrollment states or identity/section/role conflicts block the batch.

The connector owns the CSV bytes. Only `course_id,user_id,role,section_id,status,notify` are written. Role, status, and notification values are fixed to `student,active,false`. No arbitrary CSV/upload path, POST endpoint, full batch mode, diffing, UI override, user creation, course creation, or deletion options are exposed.

## Approval and delivery

Private policy version 10 adds `approved_sis_enrollment_import_fingerprints`, empty by default. Each fingerprint binds the HTTPS origin, operation, and complete normalized target. The submit confirmation also binds the generated CSV hash; a changed preflight result requires a fresh preview. Neither content-course access nor an invitation approval alone grants SIS-import access. User authorization, local policy, and MCP-client controls all remain required.

The transport posts generated UTF-8 bytes to `POST /api/v1/accounts/{root_account_id}/sis_imports?import_type=instructure_csv`. It uses the existing credential resolver and verified TLS, rejects redirects, and does not retry. Default Canvas behavior keeps batch/diff/override modes off. Imported enrollments use active status rather than regular invitations.

## Asynchronous verification and recovery

A mode-0600 receipt is reserved before POST within an owned mode-0700 directory. It records the exact target, CSV digest, imported student IDs, existing active records, and returned import ID. A crash or timeout leaves a reservation that blocks repeat submission. The receipt cannot be supplied by tool arguments, and verification requires the exact recorded target and import ID.

The submit tool returns `verified:false` with an import ID. The verify tool performs GETs only. It returns pending for initializing/created/importing; success requires `imported`, no reported warnings/errors, only the enrollment batch, expected enrollment counts, and exact active StudentEnrollment records. Each imported record must have the returned `sis_import_id`. Previously active records must still match the complete target. An import may partially succeed; no automatic rollback or second POST occurs when verification fails.

For uncertain outcomes, inspect the root account's import list and the exact course enrollments before operator-directed recovery. Do not automatically clear a receipt, resubmit, or reinterpret an invitation as an active enrollment. Keep receipts and real student data outside source control.

## References

- [Canvas SIS Imports API](https://developerdocs.instructure.com/services/canvas/resources/sis_imports)
- [Canvas SIS CSV enrollment format](https://developerdocs.instructure.com/services/canvas/sis/file.sis_csv#enrollments.csv)

Tests cover policy isolation and migration, exact identity and section matching, read-only preview/verification, bounded/generated CSV, leading zeros, duplicate/conflicting requests, receipt protection, uncertain submission, asynchronous completion, import error/count checks, enrollment provenance, and transport redirect refusal. All credential and network boundaries are mocked.
