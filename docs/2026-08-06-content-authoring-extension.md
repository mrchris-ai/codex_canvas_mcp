# Content-authoring extension and validation record

## Purpose

This record documents the expansion of the shared local Canvas MCP from a
Pages-only writer to a guarded course-content authoring tool. The repository is
the source of truth for this integration; the local desktop configuration runs
this versioned project rather than an untracked standalone script.

## Implemented capabilities

All mutations remain disabled unless the local policy is enabled for one exact
course, the caller provides the action-specific confirmation text, and the MCP
client approves the write.

- Create or update a Canvas Page.
- Create a Module.
- Add a Page, Assignment, Discussion, Classic Quiz, File reference, or external
  URL to a Module.
- Create an Assignment.
- Create a Discussion.
- Create a Classic Quiz and add supported question types.

The MCP intentionally does not expose a generic Canvas write endpoint.

## End-to-end validation

On 2026-08-06, the guarded tools were tested in a designated Canvas test shell.
All created resources were left unpublished:

1. A test Module was created.
2. A test Page, Assignment, Discussion, and Classic Quiz were created.
3. The Page, Assignment, Discussion, and Classic Quiz were placed in the Module.
4. A multiple-choice question was created in the Classic Quiz.
5. Each result was read back through the Canvas API to verify its identity,
   unpublished state, and module placement.

The first Discussion exposed a Canvas request-format issue: Canvas created the
resource but ignored its title. The tool was corrected to send Canvas's required
top-level discussion payload, then a replacement unpublished Discussion was
created and verified. The correction is included in the repository history.

## Security state after testing

The temporary test-course allowlist was removed after validation. The local
write policy is now disabled and empty, returning the MCP to read-only mode.

## Deferred capabilities

- Local file upload: requires a payload-specific approval design so the server
  cannot upload an unintended local file.
- New Quizzes: requires a separate reviewed tool contract.
- Enrollment, course provisioning, cross-listing, account administration, and
  other administrative operations: deferred to the roadmap.

## Related project records

- `README.md` describes installation, safety gates, and the current authoring
  boundary.
- `AGENTS.md` defines the engineering and security invariants.
- `ROADMAP.md` tracks intentionally deferred features.
