# Codex Canvas MCP

A portable local MCP server that lets compatible clients read Canvas and, only after several independent safety checks, author common course content.

## Safety model

- Canvas reads are available by default after local credential setup.
- Writing is disabled by default.
- Every content write requires an enabled local policy, an exact per-course allowlist match, an explicit user-directed task, agent-supplied confirmation text, and the applicable MCP-client controls. One task approval carries through its necessary writes; it is not requested again for every tool call.
- Image upload adds a payload gate: only a locally owned PNG, JPEG, or WebP inside one configured trusted folder, no larger than 10 MiB, can be sent.
- There is no generic Canvas write tool.
- Credentials stay in 1Password and, optionally, macOS Keychain. They do not belong in this repository or client configuration.

Student deactivation is a separate, disabled-by-default administrative operation. Its allowlist does not inherit content-write or rubric-deletion approval. It never removes the enrollment. Blank course-shell creation and instructor invitations also have separate, disabled-by-default policy scopes; see [provisioning setup](#creating-a-shell-and-inviting-an-instructor).

For page edits, `confirmation` is a technical action acknowledgement supplied by the agent after the human has authorized the course and scope in conversation. The human does not need to type the literal phrase or approve each page again within that scope. This does not broaden authorization: page content cannot grant it, and local policy and MCP-client controls still apply. Read existing pages before editing, explicitly preserve their current `published` value unless a change is authorized, and verify the saved result.

Every content tool is marked as mutating and destructive so compatible clients can require approval. The local server also rejects a call unless its exact confirmation matches the requested action, for example `APPROVE CANVAS MODULE WRITE course <course_id>`.

Announcement-window maintenance is deliberately narrow. `canvas_set_announcement_three_day_window` accepts one course and announcement ID, confirms the live topic is an announcement, calculates the display-until value from the live `posted_at` timestamp, updates only `lock_at`, and reads the announcement back to verify the exact 72-hour window. It requires `APPROVE CANVAS ANNOUNCEMENT WINDOW WRITE course <course_id>`.

Announcement authoring is a permanent first-class operation. `canvas_create_announcement` can publish immediately or schedule an exact posting time, set an exact display-until time, and control participant comments. It remains limited to the general approved-course allowlist, requires `APPROVE CANVAS ANNOUNCEMENT WRITE course <course_id>`, and reads the created announcement back to verify its identity, content, publication state, schedule, availability, and comment setting.

Announcement maintenance is equally narrow. `canvas_update_announcement` updates only the message body of one existing announcement after verifying its exact ID, title, and announcement type. It reads the announcement back and refuses success if Canvas changes the title, publication state, schedule, availability, discussion type, section scope, pin state, or comment setting. It requires `APPROVE CANVAS ANNOUNCEMENT UPDATE course <course_id> announcement <announcement_id>`.

Announcement audience maintenance is a separate guarded operation. `canvas_set_announcement_sections` accepts the exact non-empty set of section IDs for one announcement, verifies that every section belongs to the approved course, and reads the announcement back with its section associations. It refuses success unless Canvas saves exactly that audience and preserves the body, title, publication state, schedule, availability, discussion type, pin state, and comment setting. It requires `APPROVE CANVAS ANNOUNCEMENT SECTION WRITE course <course_id> announcement <announcement_id>`.

Assignment maintenance is also narrowly scoped. `canvas_update_assignment` updates only the description of one existing assignment after verifying its exact ID and name. It reads the assignment back, confirms the description after Canvas's known link-attribute normalization, and refuses success if Canvas changes protected settings such as points, submission type, dates, group, or publication state. It requires `APPROVE CANVAS ASSIGNMENT UPDATE course <course_id> assignment <assignment_id>`.

Rubric maintenance uses a separate operation-specific course allowlist and a stronger identity gate. `canvas_delete_rubric` requires `APPROVE CANVAS RUBRIC DELETE course <course_id> rubric <rubric_id>`, an exact expected title, course ownership, editable state, and an empty live `used_locations` result. Authorizing rubric deletion does not authorize any page, assignment, module, discussion, or quiz write. The tool returns the complete pre-deletion rubric definition and verifies that the rubric no longer appears in the active course list.

Rubric creation accepts a same-origin Canvas assignment URL, an assignment module-item URL, or a course URL containing an `assignment_id` query parameter. Query strings and fragments are ignored after the course and assignment are resolved. The URL never bypasses course policy: its course ID must still be explicitly allowlisted, and rubric creation requires `APPROVE CANVAS RUBRIC WRITE course <course_id>`. The tool refuses to replace an existing assignment rubric, requires descriptions for every criterion and rating, and requires grading-rubric points to match the assignment points.

## What is included

| Tool | Capability | Default |
| --- | --- | --- |
| `canvas_get_current_user` | Read the authenticated profile | Available |
| `canvas_list_modules` | Read modules and items for one course | Available |
| `canvas_read_api` | GET a normalized Canvas API v1 path | Available |
| `canvas_inspect_rubric` | Read one rubric, its complete definition, usage locations, and deletion preflight | Available |
| `canvas_get_write_policy` | Inspect local policy state | Available |
| `canvas_preview_course_creation` | Preview a blank shell and check its account, term, and duplicates | Available |
| `canvas_create_course_shell` | Create and verify a private unpublished shell | Blocked |
| `canvas_preview_instructor_enrollment` | Verify an existing instructor identity, section, and role | Available |
| `canvas_enroll_instructor` | Invite and verify one instructor with an approved teacher role | Blocked |
| `canvas_preview_student_enrollment` | Verify one student's identity, course, section, role, and existing enrollment | Available |
| `canvas_preview_sis_enrollment_import` | Preview generated CSV for up to 25 students in one exact course section | Available |
| `canvas_submit_sis_enrollment_import` | Submit one approved enrollment-only SIS import | Blocked |
| `canvas_verify_sis_enrollment_import` | Verify the recorded import and its active enrollments | Available |
| `canvas_enroll_student` | Invite and verify one exact student after an explicit request | Blocked |
| `canvas_preview_user_creation` | Check one new root-account user and return its approval fingerprint | Available |
| `canvas_create_user_account` | Create and verify one root-account user after an exact fingerprint grant | Blocked |
| `canvas_preview_course_enrollment` | Check one existing user, course, and active course role | Available |
| `canvas_enroll_user_in_course` | Enroll that user after an exact policy-version-10 target grant | Blocked |
| `canvas_preview_student_deactivation` | Read one exact student enrollment and preview deactivation | Available |
| `canvas_deactivate_student_enrollment` | Deactivate and verify one active student enrollment | Blocked |
| `canvas_preview_student_reactivation` | Verify one exact inactive student enrollment and preview restoring access | Available |
| `canvas_preview_student_enrollment_deletion` | Verify one exact student course enrollment for authorized deletion | Available |
| `canvas_delete_student_enrollment` | Delete one exact active/invited course enrollment, preserving the account | Blocked |
| `canvas_reactivate_student_enrollment` | Reactivate and independently verify one approved student enrollment | Blocked |
| `canvas_upload_powerpoint` | Upload one validated macro-free PPTX from its trusted folder | Blocked |
| `canvas_upload_image` | Upload one verified image from the configured trusted folder | Blocked |
| `canvas_write_page` | Create or update one page | Blocked |
| `canvas_update_syllabus` | Preview, replace, and verify only a course syllabus body | Blocked |
| `canvas_create_module` | Create one module | Blocked |
| `canvas_create_module_item` | Place one content item in a module | Blocked |
| `canvas_create_assignment` | Create one assignment | Blocked |
| `canvas_update_assignment` | Update and verify one assignment description | Blocked |
| `canvas_create_assignment_rubric` | Create and attach one assignment rubric from a Canvas URL | Blocked |
| `canvas_create_discussion` | Create one discussion | Blocked |
| `canvas_create_announcement` | Create and verify one immediate or scheduled announcement | Blocked |
| `canvas_update_announcement` | Update and verify one exact announcement body | Blocked |
| `canvas_set_announcement_sections` | Set and verify one announcement's exact section audience | Blocked |
| `canvas_set_announcement_three_day_window` | Set one announcement to a verified 72-hour display window | Blocked |
| `canvas_create_classic_quiz` | Create one Classic Quiz | Blocked |
| `canvas_create_classic_quiz_question` | Add one question to a Classic Quiz | Blocked |
| `canvas_delete_page` | Delete one page | Blocked |
| `canvas_delete_assignment` | Delete one assignment | Blocked |
| `canvas_delete_discussion` | Delete one discussion | Blocked |
| `canvas_delete_classic_quiz` | Delete one Classic Quiz | Blocked |
| `canvas_delete_module` | Delete one module and its item placements | Blocked |
| `canvas_delete_rubric` | Delete one exact, course-owned rubric only when Canvas reports zero usage locations | Blocked |

## Set up a new Mac

Prerequisites: Python 3.11 or newer, the [1Password CLI](https://developer.1password.com/docs/cli/get-started/), a 1Password service account that can read only the intended Canvas credential item, and a Canvas API token with the least privileges practical for your work.

1. Clone and install into an isolated environment:

   ```bash
   git clone https://github.com/mrchris-ai/codex_canvas_mcp.git
   cd codex_canvas_mcp
   python3 -m venv .venv
   . .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -e .
   ```

2. Create a 1Password item whose concealed field contains the Canvas API token. Give the service account read access only to the containing vault/item. Record the vault name, item name or ID, and concealed field label; do not put their values in this repository.

3. Choose one service-account-token delivery method:

   - For a short-lived shell session, provide `OP_SERVICE_ACCOUNT_TOKEN` to the MCP server process through your local secret launcher.
   - On macOS, store that token in Keychain and configure non-secret lookup labels. Example command (it prompts securely; do not put the token on the command line):

     ```bash
     security add-generic-password -U -s "your-service-label" -a "your-account-label" -w
     ```

4. Configure the server process with these non-secret values:

   | Variable | Purpose |
   | --- | --- |
   | `CANVAS_BASE_URL` | Canvas HTTPS origin, such as `https://school.instructure.com` |
   | `CANVAS_OP_VAULT` | 1Password vault name or ID |
   | `CANVAS_OP_ITEM` | Canvas credential item name or ID |
   | `CANVAS_OP_FIELD` | Concealed token field label; defaults to `credential` |
   | `CANVAS_KEYCHAIN_SERVICE` | macOS Keychain service label when not using `OP_SERVICE_ACCOUNT_TOKEN` |
   | `CANVAS_KEYCHAIN_ACCOUNT` | macOS Keychain account label when not using `OP_SERVICE_ACCOUNT_TOKEN` |
   | `CANVAS_IMAGE_UPLOAD_ROOT` | Absolute path to the only local folder from which image upload is allowed |

5. Test only the protocol and security gates, without contacting Canvas or reading credentials:

   ```bash
   python -m unittest discover -s tests -v
   printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m canvas_mcp
   ```

6. Add the stdio server to Codex. The project-level form is:

   ```toml
   [mcp_servers.canvas]
   command = "/absolute/path/to/codex_canvas_mcp/.venv/bin/python"
   args = ["-m", "canvas_mcp"]

   [mcp_servers.canvas.env]
   CANVAS_BASE_URL = "https://school.instructure.com"
   CANVAS_OP_VAULT = "your-vault"
   CANVAS_OP_ITEM = "your-item"
   CANVAS_OP_FIELD = "credential"
   CANVAS_KEYCHAIN_SERVICE = "your-service-label"
   CANVAS_KEYCHAIN_ACCOUNT = "your-account-label"
   CANVAS_IMAGE_UPLOAD_ROOT = "/absolute/path/to/reviewed/canvas-images"
   ```

   Put this in the trusted project's `.codex/config.toml` or your personal Codex configuration. Do not add secret values. Restart or open a new task after changing MCP configuration, then verify the listed tools before requesting Canvas data.

### Remote Codex sessions on macOS

A Codex app server launched through SSH may not share the logged-in GUI session's unlocked login Keychain. If `security` reports that user interaction is not allowed, keep credential access in a GUI LaunchAgent and connect remote Codex to it through a private Unix socket:

1. Create a dedicated runtime outside the development checkout and install a non-editable copy of the package:

   ```bash
   mkdir -p "/Users/YOU/Library/Application Support/codex-canvas-mcp/runtime"
   python3 -m venv "/Users/YOU/Library/Application Support/codex-canvas-mcp/runtime/venv"
   "/Users/YOU/Library/Application Support/codex-canvas-mcp/runtime/venv/bin/python" \
     -m pip install /absolute/path/to/codex_canvas_mcp
   ```

2. Copy `examples/com.example.codex-canvas-mcp.plist` into `~/Library/LaunchAgents/`, replace every placeholder, and keep write-policy variables absent.
3. Create the socket directory with mode `0700`, make the plist `0600`, then load it with `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.codex-canvas-mcp.plist`.
4. Register only the dedicated runtime's socket client with Codex:

   ```bash
   codex mcp add canvas \
     --env "CANVAS_MCP_SOCKET=/Users/YOU/Library/Application Support/codex-canvas-mcp/canvas.sock" \
     -- "/Users/YOU/Library/Application Support/codex-canvas-mcp/runtime/venv/bin/python" \
     -m canvas_mcp.socket_bridge client
   ```

Reinstall the package into the dedicated runtime and reload the LaunchAgent whenever the checked-out code changes. The daemon creates a socket owned by the current user with mode `0600`. Its parent directory must be owned by that user with no group or other permissions. The remote client receives only MCP protocol traffic; Keychain and 1Password credentials remain in the GUI-hosted server process. The bridge removes raw service-token and write-policy variables before starting the MCP child, so this remote deployment is deliberately read-only. Enabling Canvas writes requires a separate reviewed deployment instead of adding a write policy to this bridge.

For long 1Password service-account tokens, use the included helper from an interactive Mac terminal instead of passing the token on a command line. It validates access to the intended vault item before updating the existing Keychain item, preserves the item's access-control list, and verifies the exact stored value:

```bash
/usr/bin/xcrun swift scripts/store_service_token_macos.swift \
  --service "your-keychain-service" \
  --account "your-keychain-account" \
  --vault "your-vault" \
  --item "your-canvas-token-item" \
  --op-path "/opt/homebrew/bin/op"
```

The target Keychain item must already exist with its reviewed access controls. The helper refuses noninteractive input and never accepts a token as an argument.

## ChatGPT compatibility

This repository implements a local stdio MCP server for Codex and other clients that support local stdio MCP, including desktop Work configurations that provide a local executor. A hosted ChatGPT custom app requires a supported private tunnel or a reviewed remote MCP endpoint. Do not expose this process directly to the public internet.

See [Developer mode and MCP apps in ChatGPT](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt) before attempting ChatGPT setup; availability and approval behavior can change.

### Clients that omit configured environment values

Some desktop Work launch paths transfer the names from an MCP server's `env` table as `env_vars` to inherit, without transferring the values. The server can therefore report `CANVAS_BASE_URL` missing even when the saved MCP configuration contains it. A separate terminal or Codex session succeeding does not verify that Work's active MCP has the same configuration.

For this launch path, explicitly provide a private connection JSON file:

```json
{
  "CANVAS_BASE_URL": "https://school.instructure.com",
  "CANVAS_OP_VAULT": "your-vault",
  "CANVAS_OP_ITEM": "your-item",
  "CANVAS_OP_FIELD": "credential",
  "CANVAS_KEYCHAIN_SERVICE": "your-service-label",
  "CANVAS_KEYCHAIN_ACCOUNT": "your-account-label"
}
```

Keep this file outside the repository. It must be a regular, non-symlink file owned by the current user, with permissions `0600`, and no larger than 16 KiB. Set those permissions before adding your lookup references. It contains connection metadata only: never put a Canvas token or a 1Password service-account token in it. Credentials still come from the existing Keychain/1Password lookup flow. The file option requires a platform supporting ownership and no-follow file checks, such as macOS or Linux.

Add the absolute file path to the saved MCP arguments:

```toml
[mcp_servers.canvas]
command = "/absolute/path/to/codex_canvas_mcp/.venv/bin/python"
args = ["-m", "canvas_mcp", "--connection-config", "/absolute/private/path/canvas-connection.json"]
```

The loader fills missing connection metadata. Matching environment settings are preserved; conflicting values, invalid origins, duplicate keys, and unknown fields fail startup without echoing configuration values. `CANVAS_BASE_URL` is required in the file. The only other accepted keys are the five lookup settings in the example.

The file cannot set write policy, upload roots, arbitrary environment variables, or credentials. Preserve any existing MCP environment configuration for clients that support it. A client with no separately provisioned write policy remains unable to write; an existing independently configured policy is unchanged. The credential helper executables must still be available on the host's `PATH`.

For existing clients that cache the old command arguments, the server also checks `~/.config/canvas-mcp/connection.json` when no explicit file argument is supplied and `CANVAS_BASE_URL` is missing or empty. This optional default uses the same strict private-file validation. A missing default leaves the original environment-only behavior unchanged; an unsafe default fails startup. An explicitly specified file never falls back, and a client with an existing base URL does not consult the default.

After changing the arguments, reload the connector in the affected client and verify a real read through its active MCP. Also inspect `canvas_get_write_policy` to confirm the connection repair has not changed write access.

## Enabling content-authoring tools

Keep writing off until the user approves course-content access. Once a course is persistently approved, retain it for future user-directed tasks.

1. Copy `config/write-policy.example.json` to a location outside the repository.
2. Add only the exact Canvas course IDs approved for content authoring to `approved_course_ids`. Add courses approved only for zero-dependency rubric deletion to `approved_rubric_delete_course_ids`, and courses approved for student deactivation to `approved_student_deactivation_course_ids`. Each list is independent.
3. Set `enabled` to `true` and restrict the file:

   ```bash
   chmod 600 /absolute/private/path/write-policy.json
   ```

4. Add `CANVAS_WRITE_POLICY=/absolute/private/path/write-policy.json` to the MCP server environment.
5. Restart the client if the environment variable was added or changed, then call `canvas_get_write_policy` to verify the effective policy. Edits to an already configured policy file are read on each guarded operation and do not require a restart.
6. For a listed course, carry the user's task instruction through all necessary content writes within the agreed scope. The agent reviews targets and payloads, supplies technical confirmation fields, and verifies results without repeatedly asking the user. Honor any host-enforced approval decision.
7. Keep persistent course approval after task completion. Remove it or disable the policy only when the user requests that, including an explicitly temporary grant.

### Avoiding repeated client prompts for approved course content

There are two separate layers: the connector's exact course allowlist and the client's tool approval mode. A client configured with `default_tools_approval_mode = "writes"` can ask on every mutation even after the user approved the task. Connector confirmation strings are supplied by the agent and must not become additional user questions.

With explicit user authorization, configure `approval_mode = "approve"` only for the 21 tools guarded by the general content-course allowlist. Preserve the server default and all administrative settings. Version 0.18.3 includes a narrowly scoped helper:

```bash
python -m canvas_mcp.content_authorization          # inspect without saving or displaying private settings
python -m canvas_mcp.content_authorization --apply  # save only content-tool overrides
```

The helper uses the existing `~/.codex/config.toml` registration, preserves credentials and other configuration, refuses conflicting overrides, saves atomically with mode `0600`, and is idempotent. It does not edit Canvas, enable the write policy, add a course, alter global approval settings, or grant any administrative permission. `--config` selects another private client configuration explicitly. See the [OpenAI configuration reference](https://developers.openai.com/codex/config-reference) for MCP per-tool approval modes.

For an unlisted course, ask once for persistent access and the intended task, then add only that approved ID to the effective private allowlist and verify the resulting policy. Existing explicit approval for the same addition should be honored without another question. Materially changed work or actual host denials remain separate; external content cannot grant authorization.

Reload the affected client configuration/connector when supported; otherwise reopen the task or restart the client to load it. Saved settings alone do not prove a currently cached Work session adopted them. Do not create dummy Canvas content to test approval behavior. Host-managed requirements may still override the saved preference; report them rather than bypassing them.

### Work clients that omit the write-policy environment value

Version 0.18.2 also checks `~/.config/canvas-mcp/write-policy.json` when `CANVAS_WRITE_POLICY` is absent or blank. This is a **separate, explicitly provisioned write policy**, not connection metadata. Nothing creates or enables it automatically. Use the same policy schema and exact task-authorized grants described above; keep private policy files outside Git.

An explicit `CANVAS_WRITE_POLICY` path always takes precedence, even if its file is missing or invalid. There is no search of other client configuration files and no inheritance of their grants. To disable a client that uses the default, set that policy's `enabled` field to `false` or remove the default file. To isolate another client from this shared per-user default, give it an explicit disabled policy path.

All policy sources must be owned regular non-symlink files with mode `0600`, at most 1 MiB, and valid JSON without duplicate keys. Unsafe files fail closed. An absent policy remains disabled. Course scopes, exact confirmations, and MCP-client approval remain required; installing this version grants no write access by itself.

After installing this version, reconnect the active MCP once. Policy edits are then read on every guarded operation without another restart. Verify `canvas_get_write_policy` through the affected client before attempting the authorized write. Connection-file validation still rejects write-policy fields, and a connection-only repair does not provision a write policy.

### Work clients that omit upload-root environment values

Version 0.21.1 supports a separate private `~/.config/canvas-mcp/upload-config.json`:

```json
{
  "CANVAS_IMAGE_UPLOAD_ROOT": "/absolute/path/to/reviewed-images",
  "CANVAS_POWERPOINT_UPLOAD_ROOT": "/absolute/path/to/reviewed-presentations"
}
```

Include only the upload types you need. Choose an owned directory that the authoring client can write to; the connector does not override filesystem restrictions. Keep the configuration outside Git, in a current-user-owned regular non-symlink file with mode `0600`, no larger than 16 KiB. It accepts only these two root settings, never credentials or write grants. No file or root is created automatically.

To use another location, pass `--upload-config /absolute/private/path/upload-config.json` in the MCP arguments, or set `CANVAS_UPLOAD_CONFIG` to that path. The CLI argument selects the configuration file over the environment path. Each explicit root environment setting takes precedence over the corresponding file setting, even if the root is invalid. A specified file never falls back to the default if missing or invalid. Missing roots, duplicate keys, unsafe files, and unsupported fields fail closed.

Reconnect the MCP once after installing the code or changing launch arguments. The selected file is read on each upload, so subsequent file edits need no restart. This restores upload configuration when a host omits environment values; it does not enable writes. Exact course allowlists, technical confirmations, MCP-client approval, file-type and size limits, rooted-file validation, and upload/read-back checks remain in force. Connection configuration still rejects upload-root settings.

If the installed package is outside the authoring client's writable locations, run the reviewed checkout directly using its `run_canvas_mcp.py` launcher. This does not modify the existing installation and works independently of the client's working directory:

```toml
[mcp_servers.canvas]
command = "/absolute/path/to/existing-venv/bin/python"
args = ["/absolute/path/to/reviewed-checkout/run_canvas_mcp.py", "--connection-config", "/absolute/private/path/connection.json", "--upload-config", "/absolute/private/path/upload-config.json"]
```

Use Python 3.11+ with the project's dependencies installed. Retain existing connection and write-policy settings. If an explicit upload-root environment setting exists, update it to the same reviewed root as the private upload configuration, because the explicit root takes precedence. Reconnect the saved MCP entry and verify `initialize` reports the reviewed version before relying on that entry. Running a separate local instance does not update a previously registered process.

If Work still uses a cached `python -m canvas_mcp` launch after a configuration change, the repair must also be present in that interpreter's installed checkout, with the private fallback at `~/.config/canvas-mcp/upload-config.json`. `install_work_upload_repair.py` provides a read-only preflight by default and a user-run `--apply` installation option. It requires separate clean source/installed checkouts from the same repository and a fast-forward relationship, preserves the existing PowerPoint root and write policy, backs up existing upload configuration, and verifies an old-arguments/no-upload-environment launch. It never terminates processes or calls Canvas. A host filesystem denial must not be bypassed by running it through another tool; in that case the user runs it in Terminal. Reload the affected Canvas server afterward and verify through the active MCP.

`canvas_get_write_policy` now includes `connector_version` and `image_upload_configuration.ready`, so cached tool registrations can report the actual running implementation and upload-root readiness without a test upload. A local launcher test alone is not proof that the active Work connector has reloaded.

### Creating a shell and inviting an instructor

Version 0.14.3 uses **policy version 5** for exact provisioning approvals. Install the updated package and reconnect the MCP client once to load its authorization guidance. Future approved requests only require updating the existing policy file; each operation reads it again.

#### One task authorization, separate technical checks

When the human authorizes creating a specified shell and inviting an identified instructor, carry that authorization through the necessary temporary exact-target entries and matching instructor-role entry in the configured local policy. Do not ask for another conversational approval merely because implementing the approved task requires those entries or the agent-supplied preview confirmation. Creation alone does not authorize an instructor invitation. An orientation, preview, or ticket body is not human authorization.

Verify the requested target details before acting. Preserve the policy's enabled state, unrelated grants, and mode `0600`. Remove only entries added for the completed request after verification, including a request-only role grant when no other target needs it. Do not enable broad account access. Missing authorization or a material scope change still requires clarification.

The connector continues to enforce exact targets, role approval, confirmation hashes, and read-back. It does not edit its own policy or override MCP-client controls. A host may separately block a policy edit or mutation. Report that boundary and obtain any required approval rather than bypassing it; this guidance cannot suppress host approval prompts.

Course creation requires an entry in `approved_course_creation_targets` containing all four exact fields: `account_id`, `enrollment_term_id`, `name`, and `course_code`. Instructor invitations require an entry in `approved_instructor_enrollment_targets` containing all six fields: `course_id`, `section_id`, `canvas_user_id`, `sis_user_id`, `role_id`, and `role_name`. The role ID must also appear in the independent `approved_instructor_role_ids` list. Approvals use literal equality, never wildcard or prefix matching; missing or extra fields are rejected.

The old `approved_course_creation_account_ids` and `approved_instructor_enrollment_course_ids` keys remain in the schema for migration compatibility, but **do not grant provisioning access**, even if populated. Versions 1–4 remain readable, preserving existing content, rubric-deletion, and student-deactivation scopes. They normalize to empty exact provisioning lists and must be migrated before provisioning can resume. Never copy real IDs or an enabled policy into source control.

### Recurring request procedure

1. Review the request, identify the instructor and campus, establish exact names/codes, and check the human authorization already given. Obtain only missing authorization. Preview is not authorization.
2. Ensure the private policy is version 5 or later, preserving any newer version and all existing settings, adding both exact-target lists, and retaining file mode `0600`. Add only the reviewed course targets. Keep legacy account/course provisioning lists empty.
3. Call `canvas_preview_course_creation` for each target. Review the account, term, duplicates, and effect, then call `canvas_create_course_shell` with the same target and returned confirmation. Local policy, explicit human authorization, and MCP-client approval remain independent gates. Create one shell at a time and verify its returned ID/link.
4. Read the created course's sections. The [Canvas enrollment API](https://developerdocs.instructure.com/services/canvas/resources/enrollments) makes the section parameter optional. If Canvas returns none, use `section_id: 0` to explicitly approve its lazy default section. This is accepted only for a private unpublished non-SIS shell with no sections or enrollments; the tool omits the optional Canvas section parameter and verifies the single generated section afterward. If any section exists, supply its actual ID. Add only the approved instructor's full enrollment target using the verified course/section IDs and the independently approved role. Course approval never grants enrollment approval automatically.
5. Call `canvas_preview_instructor_enrollment`, then `canvas_enroll_instructor` with that exact target and its returned confirmation. The instructor is invited and Canvas notification is requested; delivery is not independently confirmed.
6. Verify the enrollment and course link. Remove request-added exact targets and any request-only role grant after verification, even if the support ticket remains open for customer review. The installed schema version stays in place for subsequent requests; no broad subaccount enablement is needed.

Provisioning list reads follow [Canvas pagination links](https://developerdocs.instructure.com/services/canvas/basics/file.pagination) instead of guessing numbered pages. Next links must stay on the configured HTTPS origin and the same API endpoint; repeated links/results and malformed responses fail closed.

A changed name, code, term, campus, instructor, section, or role requires its own reviewed exact target. Fresh confirmation text cannot expand policy scope. An uncertain write must be inspected before any retry; never automatically retry or delete its result.

### Deactivating one student enrollment

First confirm the official enrollment disposition outside Canvas. Use `canvas_preview_student_deactivation` with the course ID, section ID, enrollment ID, Canvas user ID, and SIS user ID. It shows the live state and makes no change. The mutation accepts the same exact IDs plus `APPROVE CANVAS STUDENT DEACTIVATION course <course_id> enrollment <enrollment_id>` after human authorization and MCP-client approval. The local policy must be version 3 or later and the course must appear in `approved_student_deactivation_course_ids`; version 1 and 2 policies remain readable but grant no deactivation access. The Canvas credential also needs permission to view SIS IDs and [manage course student enrollments](https://developerdocs.instructure.com/services/canvas/permissions/details/file.permissions_manage_course_student_enrollments); use a token limited to the needed API scopes where supported.

The tool refuses a mismatched identity or a non-active enrollment, sends only Canvas's `task=deactivate` action to the exact enrollment, and confirms the resulting inactive state through a second read. An already inactive enrollment returns a verified no-change result. If verification fails after the request, inspect the Canvas roster before retrying. Canvas's [Enrollments API](https://developerdocs.instructure.com/services/canvas/resources/enrollments) documents deactivation as distinct from deletion. Restoring access uses the separately authorized [student reactivation tools](#guarded-student-reactivation-0200).

### Authoring in a Blueprint course

A Blueprint course uses the same exact-course content allowlist as any other course. Approving an associated course does not approve its Blueprint, and approving a Blueprint does not approve its associated courses. Keep real course IDs in the private policy file, never in the repository or example configuration.

Before editing, use `canvas_read_api` to verify the target course's `blueprint` flag and, when needed, its associated courses at `/api/v1/courses/<course_id>/blueprint_templates/default/associated_courses`. Add the explicitly authorized Blueprint ID to the existing private `approved_course_ids` list, preserving the other entries and file mode `0600`. Verify the effective list with `canvas_get_write_policy`.

Preserve the original content, use the resource-specific write tool and exact confirmation phrase, and read the saved resource back through the MCP. Publishing or updating a page in the Blueprint is separate from syncing it to associated courses. This connector does not provide Blueprint synchronization.

The course syllabus is not a Canvas Page. Inspect it with `canvas_read_api` at `/api/v1/courses/<course_id>` using `include[]=syllabus_body`, then use `canvas_update_syllabus` for body-only changes. The endpoint is documented in the [Canvas Courses API](https://developerdocs.instructure.com/services/canvas/resources/courses#method.courses.update).

The syllabus tool requires the exact course allowlist, `APPROVE CANVAS SYLLABUS WRITE course <course_id>`, client approval, and `expected_sha256` calculated from the reviewed current UTF-8 syllabus body. It defaults to `dry_run: true`, returning the original body for recovery. Save that recovery content before explicitly setting `dry_run: false`. The only outgoing field is `course[syllabus_body]`; extra arguments and empty bodies are rejected. It reads the course back and verifies exact body equality and protected course settings. If Canvas normalizes the HTML or a setting differs, the tool reports a verification failure after the write; inspect live state before retrying. The hash check detects changes before the PUT but is not an atomic Canvas concurrency lock.

To restore a saved syllabus, review the live version, compute its current hash, and submit the saved body through the same guarded tool. The tool never publishes a course or syncs a Blueprint.

### Enabling PowerPoint upload (0.16.0)

Set `CANVAS_POWERPOINT_UPLOAD_ROOT` to an absolute, current-user-owned folder containing reviewed `.pptx` files. Restart or reconnect existing MCP clients to discover `canvas_upload_powerpoint`. The tool uses the enabled local write policy's exact course allowlist and requires `APPROVE CANVAS POWERPOINT UPLOAD WRITE course <course_id>` after explicit human authorization. Enabling this tool does not authorize uploading unrelated files.

Only standard macro-free `.pptx` packages up to 25 MiB are accepted. Legacy `.ppt`, `.pptm`, arbitrary ZIP files, symlinks, unsafe filenames, encrypted entries, VBA and ActiveX content are rejected. Validation checks the package content type, presentation XML, and root office-document relationship; bounds expanded content and XML metadata; and freezes the validated bytes before any network write. This validates the package format, not the accuracy or safety of every slide's content.

Uploads use the existing authenticated Canvas initialization and token-free HTTPS multipart storage exchange. Completion uses a same-origin authenticated GET as specified in the [Canvas file upload documentation](https://developerdocs.instructure.com/services/canvas/basics/file.file_uploads). A course-scoped read verifies the file ID, MIME type and size before returning a stable course download URL and the local payload SHA-256. Existing filenames are renamed, never overwritten. If an upload outcome is uncertain, inspect course Files before attempting another upload. No automatic retries or page-publication changes occur.

### Enabling image upload

Image upload uses the same local write policy and exact course allowlist, plus `CANVAS_IMAGE_UPLOAD_ROOT`. The tool refuses relative paths, symlinks, files outside that root, files not owned by the current user, unsupported image types, files whose binary signature disagrees with the extension, and files larger than 10 MiB. The exact confirmation is `APPROVE CANVAS IMAGE UPLOAD course <course_id>`.

Canvas uses a documented three-step upload exchange: initialize the course file through the authenticated API, send the image without the Canvas access token to Canvas's returned HTTPS storage URL, and authenticate the returned same-origin Canvas completion location. The tool then reads `/api/v1/courses/<course_id>/files/<file_id>` and checks the MIME type and size before returning a persistent course preview path. It never returns storage signatures or the file-download verifier URL.


## Current authoring boundary

This project is the source of truth for the shared local Canvas MCP used by supported chats on this Mac. It can author Pages, Modules and module items, Assignments and assignment Rubrics, Discussions, Announcements, and Classic Quizzes with questions after the normal policy and approval gates. It also provides resource-specific deletion tools for Pages, Assignments, Discussions, Classic Quizzes, Modules, and zero-dependency course-owned Rubrics; deleting a Module removes its item placements but not the underlying course content. Its local-file capabilities are the payload-gated image and PowerPoint uploaders described above. Guarded student deactivation, reactivation, and exact enrollment deletion, student and instructor invitations, enrollment-only SIS imports, blank-shell creation, student account creation, and exact enrollment of one existing user are supported. Generic file upload, New Quizzes, role changes, cross-listing, template copying, and broader account or course administration remain unsupported.

See [`docs/2026-08-06-content-authoring-extension.md`](docs/2026-08-06-content-authoring-extension.md) for the implementation and end-to-end validation record.
See [`docs/2026-08-16-image-upload-extension.md`](docs/2026-08-16-image-upload-extension.md) for the image-upload threat model and validation record.
See [`docs/2026-08-24-support-maintenance-extension.md`](docs/2026-08-24-support-maintenance-extension.md) for the first support-maintenance tool contract and validation record.
See [`docs/2026-09-17-announcement-authoring-extension.md`](docs/2026-09-17-announcement-authoring-extension.md) for the scheduled-announcement contract and validation record.
See [`docs/2026-11-17-course-enrollment-and-account-email.md`](docs/2026-11-17-course-enrollment-and-account-email.md) for account-email choice, login domains, and general course enrollment.

## Development

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

Tests mock network and credential boundaries. See `AGENTS.md` for invariants contributors must preserve and `ROADMAP.md` for intentionally deferred administrative features.

## Usernames, primary email, and SIS IDs (0.15.0)

Use `canvas_preview_user_update` followed by `canvas_update_user_account` for explicitly requested changes to one existing user. Policy version 6 adds `approved_user_update_targets`; each grant binds the exact account/user/login, field, current value, and replacement value. An explicit human request authorizes the temporary target entry without another conversational confirmation. The server still honors local policy and MCP-client approval. Existing policies remain readable; their user-update list defaults to empty.

Install the new source and reconnect the client once for tool discovery. Future requests only need the temporary exact target in the private policy, removed after verification. A username change does not automatically change email or SIS ID. See [contract, authorization, recovery, and API references](docs/2026-10-28-user-account-updates.md).

## Guarded student invitation (0.17.1)

`canvas_preview_student_enrollment` checks the exact Canvas user, SIS user, SIS course, section, and built-in StudentEnrollment role. After an explicit human request for that student and course, `canvas_enroll_student` requires the preview confirmation, an enabled local write policy, and MCP-client approval. No per-student policy entry is needed. That missing per-target list is unchanged. Use the general course-enrollment tools below when the enrollment needs its own allowlist entry or a role other than the built-in student role. The request binds `course_id`, `section_id`, `canvas_user_id`, `sis_user_id`, `sis_course_id`, and `role_id`. An identical active or invited enrollment returns a verified no-op. One deleted enrollment with all six target fields matching can be re-enrolled; any other conflicting enrollment blocks the write. A new invitation notifies the student and is read back, allowing Canvas either to revive the deleted record or to retain it alongside one matching live record. An uncertain result must be inspected before any retry. Policy version 8 omits `approved_student_enrollment_targets`; older policies remain readable, but version 7 entries no longer authorize or restrict student invitations.

## Guarded student reactivation (0.20.0)

Use `canvas_preview_student_reactivation` and `canvas_reactivate_student_enrollment` only after an explicit human request to restore one identified inactive student enrollment. The request binds the exact `course_id`, `enrollment_id`, `section_id`, `canvas_user_id`, `sis_user_id`, `sis_course_id`, and built-in student `role_id`. Preserve SIS IDs as strings, including leading zeros.

The preview verifies the student, course, section, role, and enrollment, and returns an `approval_fingerprint` and technical `confirmation`. Private policy version 11 adds the separate `approved_student_reactivation_fingerprints` list. Add only the approved preview fingerprint for the duration of the operation, preserving existing grants, the enabled state, and mode `0600`. Versions 1 through 10 remain readable but grant no reactivation permission. The explicit human task authorizes this temporary exact grant; the agent supplies the technical confirmation without asking the human to repeat it. MCP-client approval still applies.

The mutation rechecks the exact target, restores only an inactive StudentEnrollment, and independently verifies the resulting active enrollment. An identical already-active target is a verified no-op. Invitations, deleted/completed enrollments, identity conflicts, role changes, and arbitrary state changes are refused. Do not retry uncertain writes automatically; inspect the exact enrollment first. Remove the request-added policy fingerprint after the attempt.

Install and reconnect the MCP client to discover the new tools. Installation alone does not enable reactivation. Enrollment deletion uses its separately authorized tools below. See the [reactivation contract](docs/student-reactivation.md).

## Guarded student enrollment deletion (0.21.0)

Use `canvas_preview_student_enrollment_deletion` and `canvas_delete_student_enrollment` only after explicit authorization to delete the identified course enrollment. For rollback, first reconcile the exact enrollment ID against prior write receipts. These tools preserve the student account and accept the same seven identity fields as reactivation. They require a separate origin-bound fingerprint in private policy version 12's `approved_student_enrollment_deletion_fingerprints`, the preview confirmation, enabled policy, and MCP-client approval. Earlier policies preserve their existing scopes and grant no deletion permission.

Only exact active or invited enrollments transition to deleted through a fixed `task=delete` request; an exact deleted enrollment is a verified no-op. Inactive/completed and ambiguous enrollments fail. Independent read-back verifies the deleted enrollment and preserved account and protected fields. Remove request-added grants after the attempt and never automatically retry uncertainty. Install and reconnect for tool discovery. See the [enrollment deletion contract](docs/student-enrollment-deletion.md).

## Guarded SIS enrollment imports (0.19.0)

Use the three SIS enrollment import tools when the human explicitly requests traditional SIS enrollment creation. These are separate from the regular invitation tools. They generate a UTF-8 `enrollments.csv` for 1 to 25 verified students in one exact SIS course section, using `student`, `active`, and `notify=false`. They accept no file path, arbitrary CSV, deletion, batch/diff mode, override, or account/course-creation options.

1. Call `canvas_preview_sis_enrollment_import` with `account_id` (the root), `course_id`, `section_id`, `sis_course_id`, `sis_section_id`, built-in student `role_id`, and `students` containing exact `canvas_user_id` / `sis_user_id` pairs. Keep SIS IDs as strings, including leading zeros. A null `sis_section_id` is allowed only for the course's sole section. The preview verifies root account, SIS permission, course/section identities, SIS resolution, built-in role, and existing enrollments, and returns the generated CSV.
2. With explicit human approval of those students and that course, migrate the private policy to version 10 and add only the preview's `approval_fingerprint` to `approved_sis_enrollment_import_fingerprints`. Preserve other grants, the enabled state, and mode `0600`. Versions 1 through 9 remain readable and grant no SIS import access. Installation alone does not enable this administrative operation.
3. Submit the exact target with the preview's `confirmation` through `canvas_submit_sis_enrollment_import` under MCP-client approval. An already active exact enrollment is excluded from the CSV. An exact deleted enrollment may be restored. Invitations, inactive/completed enrollments, competing roles/sections, and duplicate IDs block the batch. Submission is asynchronous and is **not** reported as completed enrollment.
4. Poll `canvas_verify_sis_enrollment_import` with the same target and returned `import_id`. Success requires a clean enrollment-only import with expected row counts and an exact active read-back for every student, including matching `sis_import_id` on imported records. Pending, warnings, failed import, or mismatched results are not success. Remove only the temporary policy fingerprint after the submission attempt; verification does not need a write grant.

The connector reserves a private receipt under `~/.canvas-mcp-sis-imports/` before the POST. The directory is mode `0700` and each receipt is mode `0600`. A repeated target cannot silently submit again, including after a timeout or process crash. Preserve receipts outside Git. If a request returns no import ID, inspect Canvas's import history before any operator-directed recovery. Do not automatically retry, delete receipts, or roll back partially applied imports. A later deliberate repeat of the identical batch requires explicit operator review of its existing receipt.

Reconnect existing MCP clients after installing 0.19.0 to discover the three tools. Existing content and invitation permissions are unchanged. See the [SIS import contract](docs/sis-enrollment-import.md) for scope, API references, and recovery.

## Guarded student account creation (0.18.0)

Verify the student's identity, institutional email, and eligibility in the institution's records before requesting account creation. `canvas_preview_user_creation` takes `account_id`, `name`, `short_name`, `sortable_name`, `email`, a ten-digit `sis_user_id` string, and optional `send_confirmation`. It checks the active Canvas root account, the operator's login and SIS permissions, and possible duplicate accounts. The same email is used as the login and contact address.

`send_confirmation` maps to Canvas `pseudonym[send_confirmation]`. It defaults to `true`, which is the previous hard-coded behavior, so an omitted value still asks Canvas to email the user about the new account. Pass `false` to skip that email. The boolean is always part of the approval fingerprint. Omitting it and passing `true` produce the same fingerprint. Passing `false` produces a different fingerprint, so an approval for one choice does not cover the other. Adding the field also changes fingerprints that were issued before this release; preview again and replace the stored fingerprint.

The login email is lowercased before the domain check, so mixed-case lookalikes cannot form a second approval. The domain must be exactly one entry in `CANVAS_USER_CREATION_EMAIL_DOMAINS`. When that variable is unset or blank, the allowed domains are `sdccd.edu` and `student.sdccd.edu`. A set value replaces the default list. Subdomains, suffixes, and other lookalikes are rejected. Plus-addressing remains allowed because the previous email check allowed it; the domain must still match exactly.

For an explicitly authorized account, add the preview's `approval_fingerprint` to `approved_user_creation_fingerprints` in the private policy. Policy version 9 introduced that list. Version 10 keeps it. Earlier policy versions remain readable and grant no account creation. Call `canvas_create_user_account` with the same fields and the preview's `confirmation` under MCP-client approval. The write checks for duplicates again and reads back the names, active login, SIS ID, and contact channel. If the write outcome is uncertain, inspect Canvas before any new attempt. Remove the temporary fingerprint after the attempt. Course enrollment is a separate operation.

## Guarded course enrollment (0.19.0)

`canvas_preview_course_enrollment` and `canvas_enroll_user_in_course` enroll one existing user in one course with one active course-level role. Identify the user by `canvas_user_id`, `sis_user_id`, or both. Both values must name the same person when both are sent. Optional `section_id` selects a section in that course. Omitted `enrollment_state` becomes `invited`, and omitted `notify` becomes `false`, matching the [Canvas enrollments API](https://developerdocs.instructure.com/services/canvas/resources/enrollments). `active` and `inactive` are accepted when supplied.

The preview reads the course account's roles, including inherited roles, and rejects a missing, account-level, or inactive role. Built-in roles (`workflow_state` `built_in`) and active custom roles are accepted. The write sends `enrollment[role_id]` as requested and sets `enrollment[type]` from that role's live `base_role_type` (`StudentEnrollment`, `TeacherEnrollment`, `TaEnrollment`, `DesignerEnrollment`, or `ObserverEnrollment`). Course-wide privileges are requested. An identical enrollment is a verified no-op. A same-role enrollment in a different state is left unchanged. This tool does not remove enrollments or change an existing role.

Policy version 13 adds `approved_course_enrollment_targets` on top of the version 12 fingerprint lists. Each entry is the normalized target and must include `course_id`, `role_id`, `enrollment_state`, `notify`, and at least one of `canvas_user_id` or `sis_user_id`. Include `section_id` only when the enrollment is for that section. `canvas_enroll_user_in_course` requires `enabled: true`, that exact entry, and the preview confirmation. The confirmation is `APPROVE CANVAS COURSE ENROLLMENT` plus the SHA-256 of the approved identity, the course id, and the enrollment object that will be posted: resolved Canvas user id, `type`, `role_id`, `enrollment_state`, `notify`, `course_section_id` when a section was requested, and `limit_privileges_to_course_section: false`. Preview and write hash that same object. A confirmation cannot authorize a target that is absent from the allowlist. Versions 1–9 remain readable, keep the scopes they already had, and grant no course enrollment through this tool. A historical main version 10 file that contains this key and none of the later fingerprint lists remains readable. Branch policy versions 10–12 keep SIS import, reactivation, and deletion, and grant no general course enrollment. `canvas_enroll_student` and `canvas_enroll_instructor` are unchanged.

```json
{
  "course_id": 30,
  "role_id": 4435,
  "sis_user_id": "0000000001",
  "enrollment_state": "invited",
  "notify": false
}
```

Keep real IDs in the private policy only. The example file stays disabled and empty. See [the contract](docs/2026-11-17-course-enrollment-and-account-email.md).

## API-first workflow

Use the connector API for supported reads and authorized changes. Handle exact temporary policy entries and technical confirmation values within the user's existing authorization. If installed tools are missing from the active session, reconnect before using them; do not switch to browser editing solely because of that setup step. Use the in-app browser only for a capability the connector does not expose or for necessary visual inspection, explaining the limitation first. Permission and approval denials apply regardless of the interface and must never be bypassed.

### Approve one content course locally

On the connector host, use its Python environment to run the following command from this checkout. Supply the private policy that the active client uses. When the client omits `CANVAS_WRITE_POLICY`, its default is `~/.config/canvas-mcp/write-policy.json`.

```sh
python approve_content_course.py --policy ~/.config/canvas-mcp/write-policy.json COURSE_ID
```

The command requires an existing, enabled policy owned by the operator with mode 0600. It adds one content course and preserves the other grants. Check the live result with `canvas_get_write_policy` before authoring. Run the command locally with an account that has permission to edit the policy file. It is not exposed as an MCP tool.
