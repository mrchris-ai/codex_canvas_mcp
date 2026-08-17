# Engineering and security rules

These rules apply to the entire repository.

## Security invariants

- Never add, print, log, test with, or commit Canvas tokens, 1Password service-account tokens, Keychain values, credential exports, `.env` files, or real secret identifiers.
- Never inspect a developer's live credentials during routine tests. Unit tests must mock credential and network boundaries.
- Read access is the default capability. Keep all reads on `GET` requests to normalized `/api/v1/` paths.
- Writing is disabled when no policy is configured, when the policy is missing, or when the course is not allowlisted.
- Permitted content-authoring tools may create, update, or delete only Pages, Modules, module items, Assignments, assignment Rubrics, Discussions, and Classic Quizzes with their questions. Deletion tools must be resource-specific. The sole local-payload exception is the image-only upload tool: it must remain limited to PNG, JPEG, and WebP files no larger than 10 MiB, rooted under `CANVAS_IMAGE_UPLOAD_ROOT`, and separately confirmed for the exact course. Do not add a generic write, arbitrary-method API tool, generic local-file upload tool, enrollment tool, or administrative API tool.
- Every content write must pass all three gates: local policy enabled for that exact course, exact confirmation text, and client-side approval of the mutating MCP action. Image upload must additionally validate the trusted local root, regular-file ownership, filename, size, extension, binary signature, storage URL, and same-origin Canvas completion URL.
- Keep write-tool annotations accurate (`readOnlyHint: false`, `destructiveHint: true`). Never disguise a mutation as a read.
- Do not ship an enabled policy or a real course ID. Example policy files must remain disabled with an empty allowlist.
- Do not weaken TLS validation or credential subprocess isolation.

## Change discipline

- Preserve portability: use documented configuration, not personal paths, domains, item names, account names, or course IDs.
- Keep dependencies minimal and pinned only as tightly as maintainability requires.
- Add or update tests for every security-boundary change.
- Before committing, run the unit tests, compile the package, inspect the staged diff, and scan tracked content for credential-like strings.
- Administrative Canvas features (enrollments, course creation, cross-listing, roles, account operations, and settings) belong in `ROADMAP.md` until separately designed, reviewed, and authorized.

## Verification

From an activated virtual environment:

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

Tests must not contact Canvas, 1Password, Keychain, or any other network service.
