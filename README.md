# Codex Canvas MCP

A portable local MCP server that lets compatible clients read Canvas and, only after several independent safety checks, author common course content.

## Safety model

- Canvas reads are available by default after local credential setup.
- Writing is disabled by default.
- Every content write requires an enabled local policy, an exact per-course allowlist match, exact confirmation text, and approval in the MCP client.
- There is no generic Canvas write tool.
- Credentials stay in 1Password and, optionally, macOS Keychain. They do not belong in this repository or client configuration.

Every content tool is marked as mutating and destructive so compatible clients can require approval. The local server also rejects a call unless its exact confirmation matches the requested action, for example `APPROVE CANVAS MODULE WRITE course <course_id>`.

## What is included

| Tool | Capability | Default |
| --- | --- | --- |
| `canvas_get_current_user` | Read the authenticated profile | Available |
| `canvas_list_modules` | Read modules and items for one course | Available |
| `canvas_read_api` | GET a normalized Canvas API v1 path | Available |
| `canvas_get_write_policy` | Inspect local policy state | Available |
| `canvas_write_page` | Create or update one page | Blocked |
| `canvas_create_module` | Create one module | Blocked |
| `canvas_create_module_item` | Place one content item in a module | Blocked |
| `canvas_create_assignment` | Create one assignment | Blocked |
| `canvas_create_discussion` | Create one discussion | Blocked |
| `canvas_create_classic_quiz` | Create one Classic Quiz | Blocked |
| `canvas_create_classic_quiz_question` | Add one question to a Classic Quiz | Blocked |

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
   ```

   Put this in the trusted project's `.codex/config.toml` or your personal Codex configuration. Do not add secret values. Restart or open a new task after changing MCP configuration, then verify the listed tools before requesting Canvas data.

## ChatGPT compatibility

This repository implements a local stdio MCP server for Codex and other clients that support local stdio MCP. Current ChatGPT custom apps do not connect directly to a local stdio server. OpenAI's current guidance is to expose a private local MCP through Secure MCP Tunnel (or deploy a reviewed remote MCP endpoint), subject to plan and workspace-admin availability. Do not expose this process directly to the public internet.

See [Developer mode and MCP apps in ChatGPT](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt) before attempting ChatGPT setup; availability and approval behavior can change.

## Enabling content-authoring tools

Keep writing off unless a specific task requires it.

1. Copy `config/write-policy.example.json` to a location outside the repository.
2. Add only the exact Canvas course IDs approved for content authoring.
3. Set `enabled` to `true` and restrict the file:

   ```bash
   chmod 600 /absolute/private/path/write-policy.json
   ```

4. Add `CANVAS_WRITE_POLICY=/absolute/private/path/write-policy.json` to the MCP server environment.
5. Restart the client and call `canvas_get_write_policy` to verify the effective policy.
6. For each content write, review the exact target and payload. Supply the action-specific confirmation phrase and approve the mutating action in the client.
7. Disable the policy again when the task is complete.

If the variable is absent, the file is missing, permissions are broader than `0600`, writing is disabled, or the course is not allowlisted, the server refuses the write.

## Current authoring boundary

This project is the source of truth for the shared local Canvas MCP used by supported chats on this Mac. It can author Pages, Modules and module items, Assignments, Discussions, and Classic Quizzes with questions after the normal policy and approval gates. It does not yet upload local files, create New Quizzes, or perform account/course administration. File upload requires a separate payload-specific approval design so the MCP never sends an unintended local file to Canvas.

## Development

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

Tests mock network and credential boundaries. See `AGENTS.md` for invariants contributors must preserve and `ROADMAP.md` for intentionally deferred administrative features.
