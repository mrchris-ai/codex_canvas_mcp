# Rebuilding a Linux host install

`scripts/rebuild-box.sh` rebuilds everything a Linux host needs to run this connector from a clean or restored home directory:
- the 1Password CLI (`op` 2.42.0, GPG-verified against 1Password's code-signing key `3FEF9748469ADBE15DA7CA80AC2D62742012EA22` plus a pinned sha256);
- this repo at a pinned commit;
- a venv with a non-editable install;
- a mode-700 launcher.

It's idempotent: on a healthy host it reports `changes=0`.

## Required settings (env vars)

| Var | Meaning |
|-----|---------|
| `CANVAS_BASE_URL` | Your Canvas URL |
| `CANVAS_OP_VAULT`, `CANVAS_OP_ITEM`, `CANVAS_OP_FIELD` | Where `op` reads the Canvas token |
| `POLICY_PATH` | Absolute path of an existing write-policy file. The script **never creates or edits it**. It fails unless `enabled` is false and every approval list is empty |
| `TOKEN_ENV_NAME` | **Name** of the env var holding the 1Password service-account token (default `OP_SERVICE_ACCOUNT_TOKEN`). The value is checked for presence only, never printed or written |
| `CANVAS_COMMIT` | Commit or branch to pin (default `main`; pin a SHA for reproducible rebuilds) |
| `BOX_HOME_REL` | Home directory relative to the target root (default `home/box`) |

## Run

```bash
export CANVAS_BASE_URL=... CANVAS_OP_VAULT=... CANVAS_OP_ITEM=... POLICY_PATH=/abs/path/write-policy.json
scripts/rebuild-box.sh --dry-run                        # report only
scripts/rebuild-box.sh --target /tmp/rebuild-test --verify   # build into a clean path
scripts/rebuild-box.sh --verify                         # rebuild live paths
```

`--verify` checks the package version and tool count offline. To check end to end, run this:

```bash
scripts/verify-mcp.py <home>/apps/codex_canvas_mcp/run-grok-bot.sh <COURSE_ID>
```

It makes two tool calls: `canvas_get_write_policy` and one read-only GET of the course.

## Safety

- **No deletes.** Anything replaced is renamed `<name>.bak-<timestamp>`. To roll back, rename it back and restart the MCP client.
- **Secrets.** The launcher reads the token only from the env var and reads no secret files.
- **Write policy.** It is checked, never changed.
- **Git.** If tracked files are modified and HEAD isn't the pin, the repo is left alone.
- **MCP client.** After a live rebuild, restart the MCP server from the client so it spawns the new launcher.
