# Guarded course provisioning (0.13.0)

## Contract and authorization

Two read-only previews and two narrowly scoped mutations support development-shell workflows. They do not expose arbitrary payloads, paths, methods, enrollment states, publication, templates, SIS course identifiers, user creation, role changes, or student enrollment creation.

Policy version 4 adds independent exact-ID lists for course-creation subaccounts, instructor-enrollment courses, and instructor roles. Existing content, rubric deletion, and student deactivation lists retain their meanings. Versions 1–3 remain supported with empty provisioning scopes. Policy files must remain privately owned with mode 0600; examples are disabled and empty.

Human authorization, enabled local scope, a SHA-256 confirmation bound to every requested field, and MCP-client approval remain independent requirements. The agent supplies the confirmation after authorization. Both mutations are annotated destructive, non-read-only, and non-idempotent. Previews do not enable writes. Mutations repeat their preflight reads immediately before the POST.

## Course shell

Validate an active subaccount and an active term under its root account. Search for exact name/code collisions in the destination account, following all numbered result pages through an empty page, with a 100-page safety limit. Reject malformed or repeated results. Create a private, unpublished blank shell using the explicit term, skipping account templates and self-enrollment. Never supply a SIS identifier or trigger publication. Verify the returned course ID, account, term, name/code, state, and available privacy/ownership fields through a separate GET, then return its course link.

The institution must approve the intended development subaccount; Canvas does not provide a universal development-account classification. Creation approval does not add the new ID to other policy lists. Privacy fields omitted by Canvas are not independently verified; explicit public flags are rejected. A private unpublished result is checked, not proof of inherited account/LTI configuration or absence of institution-managed background processes.

## Instructor invitation

Verify the existing user's exact Canvas and SIS IDs, the section's course, an unpublished/available course, and the exact active role ID and label available in that account (including inherited roles). Only roles based on TeacherEnrollment are accepted. Enumerate all documented enrollment states for the user. Return a no-change result only for one matching active/invited enrollment with course-wide privileges; refuse all other prior enrollments.

POST one invitation with notification requested, the exact custom role and section, and course-wide privileges. Read back to verify its enrollment ID, identity, role, section, access scope, and invited/active state. Active is accepted because the instructor may accept before verification. Canvas notification delivery is not verified.

## Credentials and minimum API access

Use an institutional credential permitted to create courses in the approved subaccount, manage teacher enrollments in the approved course, and view SIS identifiers and account roles. Restrict token scopes when supported. This change never retrieves, updates, or broadens a credential's privileges. Required endpoints are:

- GET `/api/v1/accounts/:account_id`
- GET `/api/v1/accounts/:account_id/terms/:id` (root account)
- GET `/api/v1/accounts/:account_id/courses`
- GET `/api/v1/accounts/:account_id/roles`
- GET `/api/v1/courses/:id`
- GET `/api/v1/sections/:id`
- GET `/api/v1/users/:user_id/profile`
- GET `/api/v1/courses/:course_id/enrollments`
- POST `/api/v1/accounts/:account_id/courses`
- POST `/api/v1/courses/:course_id/enrollments`

## Recovery and concurrency

Neither POST has a connector-provided server-side idempotency key or atomic preflight lock. Serialize requests for the same target; another operator can change Canvas between the preflight and POST. Duplicate detection is a safeguard, not an atomic uniqueness guarantee. Never automatically retry an uncertain write, silently adopt a prior course, reactivate an enrollment, or delete a possible successful result. Inspect any returned ID and search Canvas before further action. Cleanup, role changes, and enrollment removal require separate review and authorization; they are not provided by these tools.

## Verification

Unit tests mock network and credentials. They cover independent policy gates and migration, exact input contracts and confirmations, account/term/identity/section/role conflicts, duplicate courses, prior enrollment states, payload boundaries, pagination failures, no-op behavior, and uncertain writes/read-back. No live course or enrollment is created by the tests. A separately authorized real request is still needed to verify end-to-end operation after deployment.

API contracts: [Courses](https://developerdocs.instructure.com/services/canvas/resources/courses), [Enrollments](https://developerdocs.instructure.com/services/canvas/resources/enrollments), [Roles](https://developerdocs.instructure.com/services/canvas/resources/roles), [Enrollment Terms](https://developerdocs.instructure.com/services/canvas/resources/enrollment_terms), and [Accounts](https://developerdocs.instructure.com/services/canvas/resources/accounts).
