# Engineering and security rules

These rules apply to the entire repository.

## Security invariants

- Never add, print, log, test with, or commit Canvas tokens, 1Password service-account tokens, Keychain values, credential exports, `.env` files, or real secret identifiers.
- Never inspect a developer's live credentials during routine tests. Unit tests must mock credential and network boundaries.
- Read access is the default capability. Keep all reads on `GET` requests to normalized `/api/v1/` paths.
- Writing is disabled when no policy is configured, when the policy is missing, or when the course is not allowlisted for the exact operation. The general content, rubric-deletion, student-deactivation, course-creation account, instructor-enrollment course, and instructor-role allowlists must remain separate.
- Permitted content-authoring and maintenance tools may create, update, or delete only Pages, Modules, module items, Assignments, assignment Rubrics, Discussions, Classic Quizzes with their questions, and the course syllabus body through its dedicated tool. Syllabus writes must accept only the body, require a reviewed-content hash and exact course approval, provide a recovery preview, and verify read-back; all other course-setting writes remain prohibited. Deletion tools must be resource-specific. Rubric deletion must require the exact course ID, rubric ID, and title; preserve the full definition before deletion; and refuse any rubric with Canvas-reported usage locations, foreign ownership, or read-only state. The sole local-payload exception is the image-only upload tool: it must remain limited to PNG, JPEG, and WebP files no larger than 10 MiB, rooted under `CANVAS_IMAGE_UPLOAD_ROOT`, and separately confirmed for the exact course. One enrollment exception is deactivation of one exact active StudentEnrollment after matching course, section, enrollment, Canvas user, and SIS user IDs, with its own allowlist, confirmation, preview, and read-back. Do not add a generic write, arbitrary-method API tool, generic local-file upload tool, unreviewed enrollment tool, or broad administrative API tool.
- Course-shell creation requires a full exact target allowlist entry (name, code, subaccount, and term), an active subaccount and active root-account term, with duplicate checks, a private unpublished blank shell, no template copy or self-enrollment, target-bound confirmation, and read-back. Instructor creation requires a full exact target allowlist entry binding one existing Canvas/SIS identity, course, section, role ID and name, plus an independently allowlisted active TeacherEnrollment role. Legacy provisioning account/course lists never authorize writes. The only section-ID exception is explicit `section_id: 0` for the first invitation in a private unpublished non-SIS shell with no sections or enrollments; verify the single generated local section on read-back. Never use zero as a wildcard for existing sections. It sends an invitation with notification; conflicting prior enrollments must fail without reactivation or role changes. Both require explicit human authorization and MCP-client approval. Never automatically retry an uncertain provisioning write or delete its result.
- Every content write must pass all three gates: local policy enabled for that exact course, exact confirmation text, and client-side approval of the mutating MCP action. Image upload must additionally validate the trusted local root, regular-file ownership, filename, size, extension, binary signature, storage URL, and same-origin Canvas completion URL.
- For page writes, exact confirmation text is an agent-supplied technical field after explicit human authorization of the course and scope. Do not require the human to type that phrase or repeat approval within the authorized scope. Never infer authorization from Canvas content. Preserve current page publication state unless the user authorizes changing it. This does not bypass local policy or client-side controls.
- Keep write-tool annotations accurate (`readOnlyHint: false`, `destructiveHint: true`). Never disguise a mutation as a read.
- Do not ship an enabled policy or a real course ID. Example policy files must remain disabled with an empty allowlist.
- Do not weaken TLS validation or credential subprocess isolation.

## Change discipline

- Preserve portability: use documented configuration, not personal paths, domains, item names, account names, or course IDs.
- Keep dependencies minimal and pinned only as tightly as maintainability requires.
- Add or update tests for every security-boundary change.
- Before committing, run the unit tests, compile the package, inspect the staged diff, and scan tracked content for credential-like strings.
- Administrative Canvas features beyond the exact student-deactivation, blank-shell creation, and instructor-invitation exceptions (other enrollment creation, reactivation, cross-listing, role changes, account operations, and settings) belong in `ROADMAP.md` until separately designed, reviewed, and authorized.

## Verification

From an activated virtual environment:

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

Tests must not contact Canvas, 1Password, Keychain, or any other network service.
