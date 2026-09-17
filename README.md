# Codex Canvas MCP

A portable local MCP server that lets compatible clients read Canvas and, only after several independent safety checks, author common course content.

## Safety model

- Canvas reads are available by default after local credential setup.
- Writing is disabled by default.
- Every content write requires an enabled local policy, an exact per-course allowlist match, exact confirmation text, and approval in the MCP client.
- Image upload adds a payload gate: only a locally owned PNG, JPEG, or WebP inside one configured trusted folder, no larger than 10 MiB, can be sent.
- There is no generic Canvas write tool.
- Credentials stay in 1Password and, optionally, macOS Keychain. They do not belong in this repository or client configuration.

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
| `canvas_upload_image` | Upload one verified image from the configured trusted folder | Blocked |
| `canvas_write_page` | Create or update one page | Blocked |
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

## ChatGPT compatibility

This repository implements a local stdio MCP server for Codex and other clients that support local stdio MCP. Current ChatGPT custom apps do not connect directly to a local stdio server. OpenAI's current guidance is to expose a private local MCP through Secure MCP Tunnel (or deploy a reviewed remote MCP endpoint), subject to plan and workspace-admin availability. Do not expose this process directly to the public internet.

See [Developer mode and MCP apps in ChatGPT](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt) before attempting ChatGPT setup; availability and approval behavior can change.

## Enabling content-authoring tools

Keep writing off unless a specific task requires it.

1. Copy `config/write-policy.example.json` to a location outside the repository.
2. Add only the exact Canvas course IDs approved for content authoring to `approved_course_ids`. Add courses approved only for zero-dependency rubric deletion to `approved_rubric_delete_course_ids`; this does not enable other course writes.
3. Set `enabled` to `true` and restrict the file:

   ```bash
   chmod 600 /absolute/private/path/write-policy.json
   ```

4. Add `CANVAS_WRITE_POLICY=/absolute/private/path/write-policy.json` to the MCP server environment.
5. Restart the client and call `canvas_get_write_policy` to verify the effective policy.
6. For each content write, review the exact target and payload. Supply the action-specific confirmation phrase and approve the mutating action in the client.
7. Disable the policy again when the task is complete.

If the variable is absent, the file is missing, permissions are broader than `0600`, writing is disabled, or the course is not allowlisted, the server refuses the write.

### Enabling image upload

Image upload uses the same local write policy and exact course allowlist, plus `CANVAS_IMAGE_UPLOAD_ROOT`. The tool refuses relative paths, symlinks, files outside that root, files not owned by the current user, unsupported image types, files whose binary signature disagrees with the extension, and files larger than 10 MiB. The exact confirmation is `APPROVE CANVAS IMAGE UPLOAD course <course_id>`.

Canvas uses a documented three-step upload exchange: initialize the course file through the authenticated API, send the image without the Canvas access token to Canvas's returned HTTPS storage URL, and authenticate the returned same-origin Canvas completion location. The tool then reads `/api/v1/courses/<course_id>/files/<file_id>` and checks the MIME type and size before returning a persistent course preview path. It never returns storage signatures or the file-download verifier URL.

## Current authoring boundary

This project is the source of truth for the shared local Canvas MCP used by supported chats on this Mac. It can author Pages, Modules and module items, Assignments and assignment Rubrics, Discussions, Announcements, and Classic Quizzes with questions after the normal policy and approval gates. It also provides resource-specific deletion tools for Pages, Assignments, Discussions, Classic Quizzes, Modules, and zero-dependency course-owned Rubrics; deleting a Module removes its item placements but not the underlying course content. Its only local-file capability is the payload-gated image uploader described above. Generic file upload, New Quizzes, enrollments, cross-listing, provisioning, and account/course administration remain unsupported.

See [`docs/2026-08-06-content-authoring-extension.md`](docs/2026-08-06-content-authoring-extension.md) for the implementation and end-to-end validation record.
See [`docs/2026-08-16-image-upload-extension.md`](docs/2026-08-16-image-upload-extension.md) for the image-upload threat model and validation record.
See [`docs/2026-08-24-support-maintenance-extension.md`](docs/2026-08-24-support-maintenance-extension.md) for the first support-maintenance tool contract and validation record.
See [`docs/2026-09-17-announcement-authoring-extension.md`](docs/2026-09-17-announcement-authoring-extension.md) for the scheduled-announcement contract and validation record.

## Development

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

Tests mock network and credential boundaries. See `AGENTS.md` for invariants contributors must preserve and `ROADMAP.md` for intentionally deferred administrative features.
