#!/usr/bin/env bash
# rebuild-box.sh: rebuild a Linux host install of codex_canvas_mcp (1Password CLI, venv, launcher).
# Idempotent. Never prints, logs or writes a token or credential; it only checks that
# the named env var is non-empty. Never creates or edits the Canvas write-policy file.
# Never deletes: anything it must replace is moved aside to <name>.bak-<timestamp>.
#
# Usage: rebuild-box.sh [--target DIR] [--dry-run] [--commit SHA] [--policy PATH]
#                       [--skip-last30days] [--verify]
set -euo pipefail
set +x
umask 077

# ---- site settings (set these via env vars; no defaults that identify a person) ----
need() { [ -n "${!1:-}" ] || { echo "set $1 (see docs/box-rebuild.md)" >&2; exit 2; }; }
BOX_HOME_REL="${BOX_HOME_REL:-home/box}"
CANVAS_REPO_URL="${CANVAS_REPO_URL:-https://github.com/mrchris-ai/codex_canvas_mcp.git}"
CANVAS_COMMIT="${CANVAS_COMMIT:-main}"
CANVAS_EXPECT_VERSION="${CANVAS_EXPECT_VERSION:-0.21.2}"
CANVAS_EXPECT_TOOLS="${CANVAS_EXPECT_TOOLS:-48}"
CERTIFI_VERSION="${CERTIFI_VERSION:-2026.7.22}"
need CANVAS_BASE_URL; need CANVAS_OP_VAULT; need CANVAS_OP_ITEM
CANVAS_OP_FIELD="${CANVAS_OP_FIELD:-credential}"
TOKEN_ENV_NAME="${TOKEN_ENV_NAME:-OP_SERVICE_ACCOUNT_TOKEN}"   # name only, never the value
need POLICY_PATH
LAST30_REPO_URL="${LAST30_REPO_URL:-}"
LAST30_COMMIT="${LAST30_COMMIT:-}"
WITH_LAST30="${WITH_LAST30:-0}"
# ---- end site settings -------------------------------------------------------

OP_VERSION="2.42.0"
OP_URL="https://cache.agilebits.com/dist/1P/op2/pkg/v${OP_VERSION}/op_linux_amd64_v${OP_VERSION}.zip"
OP_SHA256="8585aa02644f71c05b268125d0b474fd0d8ed606176b3878517cb79d280cfc92"   # op binary inside the zip
OP_KEY_URL="https://downloads.1password.com/linux/keys/1password.asc"
OP_KEY_FPR="3FEF9748469ADBE15DA7CA80AC2D62742012EA22"   # 1Password code-signing key

TARGET="/"
DRY_RUN=0
VERIFY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --commit) CANVAS_COMMIT="$2"; shift 2 ;;
    --policy) POLICY_PATH="$2"; shift 2 ;;
    --skip-last30days) WITH_LAST30=0; shift ;;
    --verify) VERIFY=1; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

TS="$(date +%Y%m%d-%H%M%S)"
H="${TARGET%/}/${BOX_HOME_REL}"
REPO="$H/apps/codex_canvas_mcp"
VENV="$REPO/.venv"
LAUNCHER="$REPO/run-grok-bot.sh"
OP_DIR="$H/.local/bin"
OP_BIN="$OP_DIR/op"
OP_CFG="$H/.config/op"
LAST30="$H/apps/last30days-skill"
CHANGES=0; FAILS=0

log()  { printf '%s\n' "$*"; }
ok()   { log "  ok      $*"; }
plan() { CHANGES=$((CHANGES+1)); if [ "$DRY_RUN" = 1 ]; then log "  WOULD   $*"; else log "  DO      $*"; fi; }
fail() { FAILS=$((FAILS+1)); log "  FAIL    $*" >&2; }
run()  { if [ "$DRY_RUN" = 0 ]; then "$@"; fi; }
move_aside() { # never delete; rename with timestamp
  local p="$1"; if [ -e "$p" ] || [ -L "$p" ]; then plan "move aside $p -> $p.bak-$TS"; run mv "$p" "$p.bak-$TS"; fi; }

log "rebuild-box.sh  target=$TARGET  home=$H  dry_run=$DRY_RUN  $(date '+%Y-%m-%d %H:%M %Z')"

# ---- 0. preflight ------------------------------------------------------------
log "[0] preflight"
for c in git python3 curl unzip gpg sha256sum; do
  command -v "$c" >/dev/null && ok "$c found" || fail "$c missing"
done
if [ -n "${!TOKEN_ENV_NAME:-}" ]; then ok "\$$TOKEN_ENV_NAME is set (value not shown)"
else fail "\$$TOKEN_ENV_NAME is not set; the host must inject it (launcher will refuse to start)"; fi

# ---- 1. write policy: check only, never create or edit ------------------------
log "[1] Canvas write policy (read-only check): $POLICY_PATH"
if [ ! -f "$POLICY_PATH" ]; then
  fail "policy file missing. This script will NOT create it; restore it from backup by hand."
else
  if python3 - "$POLICY_PATH" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))
lists = {k: v for k, v in p.items() if isinstance(v, list)}
bad = [k for k, v in lists.items() if v]
print(f"  policy  version={p.get('version')} enabled={p.get('enabled')} list_fields={len(lists)} nonempty={bad or 'none'}")
sys.exit(0 if p.get("enabled") is False and not bad and lists else 1)
PY
  then ok "policy disabled with all approval lists empty"
  else fail "policy is NOT disabled-with-empty-lists. Stop and ask the owner; the script will not change it."; fi
  m="$(stat -c %a "$POLICY_PATH")"; dm="$(stat -c %a "$(dirname "$POLICY_PATH")")"
  [ "$m" = 600 ] && [ "$dm" = 700 ] && ok "policy perms file=$m dir=$dm" \
    || log "  WARN    policy perms file=$m dir=$dm (expected 600/700; not changed by this script)"
fi

# ---- 2. 1Password CLI ---------------------------------------------------------
log "[2] 1Password CLI $OP_VERSION at $OP_BIN"
cur_sha=""; [ -f "$OP_BIN" ] && cur_sha="$(sha256sum "$OP_BIN" | cut -d' ' -f1)"
if [ "$cur_sha" = "$OP_SHA256" ]; then
  ok "op binary matches pinned sha256"
  [ "$(stat -c %a "$OP_BIN")" = 755 ] || { plan "chmod 755 $OP_BIN"; run chmod 755 "$OP_BIN"; }
else
  [ -n "$cur_sha" ] && log "  note    existing op sha256 differs from pin"
  plan "download $OP_URL, verify GPG signature ($OP_KEY_FPR) and sha256, install to $OP_BIN"
  if [ "$DRY_RUN" = 0 ]; then
    tmp="$(mktemp -d)"; export GNUPGHOME="$tmp/gnupg"; mkdir -m 700 "$GNUPGHOME"
    curl -sSfL "$OP_URL" -o "$tmp/op.zip"
    curl -sSfL "$OP_KEY_URL" -o "$tmp/key.asc"
    gpg -q --import "$tmp/key.asc" 2>/dev/null
    (cd "$tmp" && unzip -q op.zip op op.sig)
    gpg --status-fd 1 --verify "$tmp/op.sig" "$tmp/op" 2>/dev/null | grep -q "VALIDSIG $OP_KEY_FPR" \
      || { fail "op GPG signature did not verify against $OP_KEY_FPR"; exit 1; }
    [ "$(sha256sum "$tmp/op" | cut -d' ' -f1)" = "$OP_SHA256" ] || { fail "op sha256 mismatch"; exit 1; }
    ok "op signature and sha256 verified"
    mkdir -p "$OP_DIR"; chmod 755 "$OP_DIR" 2>/dev/null || true
    move_aside "$OP_BIN"
    install -m 755 "$tmp/op" "$OP_BIN"
    unset GNUPGHOME
    log "  note    temp download left at $tmp (no deletes)"
  fi
fi
if [ -d "$OP_CFG" ]; then
  [ "$(stat -c %a "$OP_CFG")" = 700 ] && ok "$OP_CFG is 700" || { plan "chmod 700 $OP_CFG (was $(stat -c %a "$OP_CFG"))"; run chmod 700 "$OP_CFG"; }
  while IFS= read -r f; do plan "chmod 600 $f"; run chmod 600 "$f"; done \
    < <(find "$OP_CFG" -maxdepth 1 -type f -perm /077 2>/dev/null)
else
  plan "mkdir -m 700 $OP_CFG (op fills it on first use)"; run mkdir -p "$OP_CFG"; run chmod 700 "$OP_CFG"
fi

# ---- 3. codex_canvas_mcp repo at pinned commit ---------------------------------
log "[3] codex_canvas_mcp repo at $REPO (pin $CANVAS_COMMIT)"
if [ -d "$REPO/.git" ]; then
  head="$(git -C "$REPO" rev-parse HEAD)"
  pin_full="$(git -C "$REPO" rev-parse --verify -q "$CANVAS_COMMIT^{commit}" || true)"
  if [ -n "$pin_full" ] && [ "$head" = "$pin_full" ]; then ok "HEAD is pinned commit ${head:0:7}"
  elif [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]; then
    fail "repo HEAD ${head:0:7} != pin and tracked files are modified; not touching it"
  else
    plan "git fetch origin and checkout --detach $CANVAS_COMMIT (was ${head:0:7})"
    run git -C "$REPO" fetch -q origin
    run git -C "$REPO" checkout -q --detach "$CANVAS_COMMIT"
  fi
else
  [ -e "$REPO" ] && move_aside "$REPO"
  plan "git clone $CANVAS_REPO_URL -> $REPO and checkout --detach $CANVAS_COMMIT"
  if [ "$DRY_RUN" = 0 ]; then
    mkdir -p "$(dirname "$REPO")"
    git clone -q --no-checkout "$CANVAS_REPO_URL" "$REPO"
    git -C "$REPO" checkout -q --detach "$CANVAS_COMMIT"
  fi
fi

# ---- 4. venv with NON-editable install ----------------------------------------
log "[4] venv $VENV (non-editable install)"
venv_ok=0
if [ -x "$VENV/bin/python" ] && [ -d "$REPO/src/canvas_mcp" ]; then
  if "$VENV/bin/python" - "$REPO/src/canvas_mcp" "$CANVAS_EXPECT_VERSION" <<'PY'
import filecmp, pathlib, sys, sysconfig, canvas_mcp
src = pathlib.Path(sys.argv[1]); inst = pathlib.Path(canvas_mcp.__file__).parent
site = pathlib.Path(sysconfig.get_paths()["purelib"])
editable = inst.resolve() == src.resolve() or any(site.glob("__editable__*")) or any(
    "codex_canvas_mcp" in p.read_text() for p in site.glob("*.pth"))
files = sorted(p.name for p in src.glob("*.py"))
_, mismatch, errors = filecmp.cmpfiles(src, inst, files, shallow=False)
print(f"  venv    version={canvas_mcp.__version__} editable={editable} stale_files={mismatch + errors or 'none'}")
sys.exit(0 if canvas_mcp.__version__ == sys.argv[2] and not editable and not (mismatch or errors) else 1)
PY
  then venv_ok=1; ok "installed package matches source, non-editable"; fi
fi
if [ "$venv_ok" = 0 ]; then
  [ -e "$VENV" ] && move_aside "$VENV"
  plan "python3 -m venv $VENV; pip install certifi==$CERTIFI_VERSION; pip install --no-deps ."
  if [ "$DRY_RUN" = 0 ]; then
    python3 -m venv "$VENV"
    "$VENV/bin/python" -m pip install -q --disable-pip-version-check "certifi==$CERTIFI_VERSION"
    (cd "$REPO" && "$VENV/bin/python" -m pip install -q --disable-pip-version-check --no-deps .)
  fi
fi

# ---- 5. launcher (mode 700) ----------------------------------------------------
log "[5] launcher $LAUNCHER"
render_launcher() { cat <<L
#!/bin/bash
# Generated by rebuild-box.sh. Reads no secret files; the token comes only from the
# host-injected env var \$$TOKEN_ENV_NAME and is never printed.
set -euo pipefail
export PATH="$OP_DIR:/usr/local/bin:\$PATH"
export OP_CONFIG_DIR="$OP_CFG"
export CANVAS_BASE_URL="$CANVAS_BASE_URL"
export CANVAS_OP_VAULT="$CANVAS_OP_VAULT"
export CANVAS_OP_ITEM="$CANVAS_OP_ITEM"
export CANVAS_OP_FIELD="$CANVAS_OP_FIELD"
export CANVAS_WRITE_POLICY="$POLICY_PATH"
if [ -z "\${$TOKEN_ENV_NAME:-}" ]; then
  echo "run-grok-bot.sh: $TOKEN_ENV_NAME is not set; the host must inject it." >&2
  exit 1
fi
export OP_SERVICE_ACCOUNT_TOKEN="\${$TOKEN_ENV_NAME}"
unset $TOKEN_ENV_NAME
exec "$VENV/bin/python" -m canvas_mcp "\$@"
L
}
want="$(render_launcher)"
if [ -f "$LAUNCHER" ] && [ "$(cat "$LAUNCHER")" = "$want" ]; then
  ok "launcher content matches"
else
  if [ -f "$LAUNCHER" ]; then
    log "  diff    (existing vs rendered; no secrets in either)"
    diff <(cat "$LAUNCHER") <(printf '%s\n' "$want") | sed -E 's/ops_[A-Za-z0-9_-]+/<redacted>/g; s/^/          /' || true
    move_aside "$LAUNCHER"
  fi
  plan "write $LAUNCHER"
  if [ "$DRY_RUN" = 0 ]; then mkdir -p "$REPO"; printf '%s\n' "$want" > "$LAUNCHER"; fi
fi
if [ -f "$LAUNCHER" ] && [ "$(stat -c %a "$LAUNCHER")" = 700 ]; then ok "launcher mode 700"
else plan "chmod 700 $LAUNCHER"; run chmod 700 "$LAUNCHER"; fi

# ---- 6. optional extra skill repo (off by default) ------------------------------
if [ "$WITH_LAST30" = 1 ]; then
  log "[6] last30days-skill repo at $LAST30 (pin $LAST30_COMMIT)"
  if [ -d "$LAST30/.git" ]; then
    h="$(git -C "$LAST30" rev-parse --short=7 HEAD)"
    [ "$h" = "${LAST30_COMMIT:0:7}" ] && ok "HEAD is $h" || log "  note    HEAD is $h, pin is $LAST30_COMMIT (left as is; update the pin or check out by hand)"
  else
    plan "git clone $LAST30_REPO_URL -> $LAST30 at $LAST30_COMMIT"
    if [ "$DRY_RUN" = 0 ]; then
      mkdir -p "$(dirname "$LAST30")"; git clone -q "$LAST30_REPO_URL" "$LAST30"
      git -C "$LAST30" checkout -q --detach "$LAST30_COMMIT"
    fi
  fi
fi

# ---- 7. optional offline verify (no Canvas calls) ------------------------------
if [ "$VERIFY" = 1 ] && [ "$DRY_RUN" = 0 ]; then
  log "[7] verify (offline: version + tool count; no Canvas calls)"
  "$VENV/bin/python" - "$CANVAS_EXPECT_VERSION" "$CANVAS_EXPECT_TOOLS" <<'PY' && ok "version and tool count match" || fail "version/tool count mismatch"
import sys, canvas_mcp
from canvas_mcp import server
print(f"  verify  version={canvas_mcp.__version__} server_version={server.SERVER_VERSION} tools={len(server.TOOLS)}")
sys.exit(0 if canvas_mcp.__version__ == sys.argv[1] and len(server.TOOLS) == int(sys.argv[2]) else 1)
PY
  "$OP_BIN" --version | sed 's/^/  verify  op=/'
fi

log "done: changes=$CHANGES failures=$FAILS dry_run=$DRY_RUN"
[ "$FAILS" = 0 ]
