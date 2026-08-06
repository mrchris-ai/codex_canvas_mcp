# Security

## Reporting a vulnerability

Do not open a public issue containing tokens, policy contents, course data, or exploit details. Contact the repository owner privately and rotate any credential that may have been exposed.

## Trust model

This server runs locally with the permissions of its user. Canvas authorization is determined by the Canvas API token stored in 1Password. A read-only default in this server does not reduce the underlying token's Canvas permissions.

The local write policy is an additional application gate, not an authorization substitute. A policy file must be owned by the current user and have mode `0600`. The repository's example policy is intentionally disabled and must never contain real course IDs.

The MCP client is a separate safety boundary. Keep confirmation enabled for the mutating page tool and review the complete title, body, publication state, course ID, and page slug before approving it.
