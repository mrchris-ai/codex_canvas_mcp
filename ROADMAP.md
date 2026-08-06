# Roadmap

The current release intentionally supports Canvas reads plus tightly guarded authoring of Pages, Modules/module items, Assignments, Discussions, and Classic Quizzes/questions. The following capabilities are deferred. None should be implemented as a generic Canvas write passthrough.

## Safety design required before implementation

Each future feature needs its own narrow tool contract, least-privilege credential review, explicit target allowlist, dry-run or preview output, exact confirmation phrase, client-side approval, audit-safe logging, rollback guidance, and tests proving cross-course isolation.

## Deferred feature areas

### File upload and New Quizzes

- File upload must require approval of the exact file payload, not merely a course-level write approval.
- New Quizzes needs a reviewed tool contract; it is distinct from the Classic Quiz API.

### Sandbox and course-shell provisioning

- Create a sandbox or course shell from an approved account or subaccount.
- Require an explicit institutional template and ownership assignment.
- Prevent accidental creation in production subaccounts.

### Cross-listing

- Preview parent and child enrollment effects before making a change.
- Verify term, account, and section compatibility.
- Design a separately confirmed undo path.

### User administration

- Resolve identities without exposing unnecessary profile data.
- Separate enrollment, role changes, invitations, and removals into distinct actions.
- Require role and course constraints in local policy.

### Maintenance

- Report stale content, broken links, unpublished items, and configuration drift using reads first.
- Keep fixes resource-specific and reviewable.
- Add export/backup evidence before any bulk maintenance operation.

## Explicit non-goals

- Arbitrary Canvas HTTP methods or endpoints
- Bulk mutation from an unreviewed prompt
- Wildcard course or account allowlists
- Enabled write policy in source control
- Credential storage inside this repository
