# Image upload extension — 2026-08-16

## Decision

Add one narrow `canvas_upload_image` tool. Do not add a generic local-file or arbitrary Canvas request tool.

The capability was reopened for an explicitly authorized unpublished-course page update after previously being held at the roadmap boundary. Existing read, content-authoring, rubric, and deletion behavior remains unchanged.

## Tool contract

Inputs:

- positive Canvas course ID;
- absolute local image path;
- exact `APPROVE CANVAS IMAGE UPLOAD course <course_id>` confirmation.

Independent gates:

1. The normal private write policy is enabled and contains the exact course ID.
2. `CANVAS_IMAGE_UPLOAD_ROOT` is configured as an absolute, current-user-owned, non-symlink directory.
3. The requested file is a current-user-owned regular file inside that root and is not a symlink.
4. Its filename is conservative, its size is 1 byte through 10 MiB, and its extension and binary signature agree as PNG, JPEG, or WebP.
5. Canvas initializes the upload through `/api/v1/courses/<course_id>/files`.
6. The returned storage URL is HTTPS, has no URL credentials, and is not a local/private literal host. The Canvas access token is not sent to this URL.
7. Every opaque `upload_params` value is preserved, and the `file` multipart part is last as required by Canvas.
8. Storage redirects are captured rather than followed. The completion location must return to the configured Canvas origin and normalized API path before authentication is attached.
9. A course-scoped file read verifies the final MIME type and byte size. Tool output omits storage signatures and download-verifier URLs.

Duplicate names use Canvas's `rename` behavior. The tool does not overwrite or delete an existing file.

## Verification

The unit suite covers the exact policy/confirmation gate, trusted-root and symlink boundaries, extension/signature checks, HTTPS storage URL constraints, multipart field order, the documented three-step Canvas exchange, course-scoped read-back, and mutating tool annotations. Tests mock all network and credential boundaries.

Run before use:

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
git diff --check
```

The live course upload and page integration are operational evidence, not part of the mocked unit suite. Keep the target unpublished and restore the local course allowlist after the bounded write.
