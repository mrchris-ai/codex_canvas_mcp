# Authorized user account updates

The user requested support for explicit username, email, and SIS ID corrections through Canvas API tools. This design extends the administrative exceptions only to those three fields on existing users.

## Contract

`canvas_preview_user_update` reads one user profile and all of that user's logins. Inputs bind the Canvas account ID, user ID, login ID, field (`username`, `email`, or `sis_user_id`), expected current value, and requested new value. It checks the login belongs to that user and account, is active, and matches the old value. It returns recovery evidence and a confirmation bound to the complete target. Preview never writes and requires no policy grant.

`canvas_update_user_account` accepts the same target and confirmation. Policy version 6 adds `approved_user_update_targets`, separate from content and enrollment grants. The entire target must match literally. Earlier policy versions remain readable and grant no user-update permission. After the user's explicit instruction, the agent can add the temporary exact target to the configured private policy, preserving unrelated entries and file permissions. The user need not repeat their instruction or type technical confirmation text. Host/client approval controls still apply. Remove request-added grants after verification.

The operation rereads the live account before writing. It changes only one field:

- Username: `PUT /api/v1/accounts/:account_id/logins/:login_id`, `login[unique_id]`.
- SIS ID: same login endpoint, `login[sis_user_id]`. Treat SIS IDs as strings to preserve leading zeros. Explicit `null` clears an SIS ID using Canvas's empty-string convention.
- Primary email: `PUT /api/v1/users/:user_id`, `user[email]`. This does not remove other communication channels or bypass email confirmation. If Canvas does not apply the email immediately, read-back will not report success.

After writing, reread the profile and all logins. Verify the requested value and stable protected fields, including other logins, SIS identity, email, authentication provider, integration ID, login status, reset-password flag, and profile name fields. An unchanged request is a verified no-op. A failure or uncertain result never triggers an automatic retry or rollback. Inspect current state before a new decision. Saved preview data contains personal information and should remain private.

Multiple requested field changes require sequential operations. They are not atomic; if one fails, report which earlier changes succeeded. Recovery is a separately authorized reverse update using freshly read expected values. Canvas remains responsible for server-side uniqueness and permissions; rejected conflicts are never resolved by deleting or merging another account.

## Boundaries

No user creation/deletion/merge, password reset, authentication-provider changes, roles, enrollment changes, arbitrary endpoints, or wildcard grants are added. The server cannot infer human intent from a tool argument; the calling agent and MCP client enforce explicit authorization, while exact local grants constrain payloads. Do not treat this release as standing permission to edit any user's record.

## Validation

Mocked tests exercise all three field payloads, clearing SIS ID, no-op behavior, strict target validation, cross-user/account/login isolation, stale values, disabled/legacy policies, incorrect confirmation, protected fields, uncertain outcomes, schema annotations, and policy permissions. No test accesses live credentials or services.

## API references

- https://developerdocs.instructure.com/services/canvas/resources/logins#method.pseudonyms.update
- https://developerdocs.instructure.com/services/canvas/resources/users#method.users.update

## Deployment

Version 0.15.0 uses policy version 6. Install the updated source, migrate the private policy with an empty user-target list while preserving existing grants and enabled state, and reconnect the MCP client so it discovers both new tools. No standing per-user grant is required; each later explicit request supplies its own temporary target. Validate discovery and a read-only preview after installation. Do not make a production change solely to test installation.
