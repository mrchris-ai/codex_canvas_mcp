# Roadmap

The current release intentionally supports Canvas reads plus tightly guarded authoring of Pages, Modules/module items, Assignments and assignment Rubrics, Discussions, and Classic Quizzes/questions. The following capabilities are deferred. None should be implemented as a generic Canvas write passthrough.

## Safety design required before implementation

Each future feature needs its own narrow tool contract, least-privilege credential review, explicit target allowlist, dry-run or preview output, exact confirmation phrase, client-side approval, audit-safe logging, rollback guidance, and tests proving cross-course isolation.

## Deferred feature areas

### Generic file upload and New Quizzes

- Image-only upload is implemented with trusted-root, ownership, size, type/signature, per-course policy, exact confirmation, storage-host, completion-origin, and post-upload verification gates. A separately gated, payload-validated `.pptx` uploader is now implemented. Other file types remain deferred and require a payload-specific review.
- Generic document, archive, audio, video, and other local-file upload remain unsupported.
- New Quizzes needs a reviewed tool contract; it is distinct from the Classic Quiz API.

### Extended course-shell provisioning

- Blank private unpublished shells are implemented for exact approved subaccounts, with explicit term, duplicate checks, and read-back. Instructor ownership is assigned through the separately approved instructor-invitation tool.
- Template copying, root-account creation, publication, SIS identifiers, and bulk provisioning remain deferred.
- Local operators must approve only intended development subaccounts; the connector cannot infer institutional production/development classification.

### Cross-listing

- Preview parent and child enrollment effects before making a change.
- Verify term, account, and section compatibility.
- Design a separately confirmed undo path.

### User administration

- Explicitly authorized username, primary email, and SIS ID updates are implemented through exact-target preview/update tools. Other account operations remain deferred.

- One-student deactivation is implemented through a dedicated preview and guarded mutation. It does not remove an enrollment.
- Resolve identities without exposing unnecessary profile data.
- One exact instructor invitation is implemented for an existing Canvas/SIS identity and an approved TeacherEnrollment role. Existing conflicts are refused.
- Student enrollment creation, reactivation, role changes, removals, and broader invitations remain deferred.
- Require role and course constraints in local policy.

### Maintenance

- Syllabus-body editing is implemented through `canvas_update_syllabus`, restricted to `syllabus_body` with a recovery preview, reviewed-content hash, exact course confirmation, and read-back verification. Generic course-setting updates remain deferred.
- Blueprint synchronization needs a separate review of associated-course impact and pending changes; authorization to edit Blueprint content alone must not trigger a sync.

- Report stale content, broken links, unpublished items, and configuration drift using reads first.
- Keep fixes resource-specific and reviewable.
- Add export/backup evidence before any bulk maintenance operation.
- Course-rubric inspection and deletion are implemented only for one exact, course-owned, editable rubric with zero Canvas-reported usage locations. Association removal, assessed-rubric deletion, account-owned rubric deletion, archiving, restoration, and bulk rubric maintenance remain deferred.

## Explicit non-goals

- Arbitrary Canvas HTTP methods or endpoints
- Bulk mutation from an unreviewed prompt
- Wildcard course or account allowlists
- Enabled write policy in source control
- Credential storage inside this repository
