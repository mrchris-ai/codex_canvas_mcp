# Security

## Reporting a vulnerability

Do not open a public issue containing tokens, policy contents, course data, or exploit details. Contact the repository owner privately and rotate any credential that may have been exposed.

## Trust model

This server runs locally with the permissions of its user. Canvas authorization is determined by the Canvas API token stored in 1Password. A read-only default in this server does not reduce the underlying token's Canvas permissions.

The local write policy is an additional application gate, not an authorization substitute. A policy file must be owned by the current user and have mode `0600`. The repository's example policy is intentionally disabled and must never contain real course IDs.

The MCP client is a separate safety boundary. Keep confirmation enabled for every mutating content tool and review the complete target, payload, publication state, schedule and availability timestamps when applicable, and course ID before approving it.

Announcement-body updates require an exact course ID, announcement ID, and expected title. The server verifies the live target is an announcement, sends only the `message` field, reads the record back, and treats any change to protected announcement settings as a failed operation.

Image upload is not a general filesystem capability. `CANVAS_IMAGE_UPLOAD_ROOT` must name an absolute, current-user-owned, non-symlink directory. The server accepts only current-user-owned regular PNG, JPEG, and WebP files inside that root, verifies both extension and binary signature, rejects symlinks and files over 10 MiB, and derives the Canvas filename from the validated local basename. The image-upload confirmation is separate from other content writes.

The Canvas access token is sent only to the configured Canvas origin. It is never sent to the upload URL returned by Canvas. The upload URL must be HTTPS and cannot use local/private literal hosts or URL credentials. Redirects from the storage upload are not followed automatically; the completion location must resolve back to the configured Canvas origin and a normalized `/api/v1/` path before the authenticated completion request is made.

Assignment URLs supplied to rubric creation are accepted only when they use the configured Canvas origin (or are root-relative). The server extracts identifiers, verifies the exact course against the local allowlist, resolves module-item URLs through a read, and sends writes only to the configured Canvas API origin. URL input never authorizes a course and never controls the destination host.
