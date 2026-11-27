#!/usr/bin/env bash
# DevPipeline installer — idempotent, uses only the official Claude Code plugin CLI.
#
#   scripts/install.sh [--without-ui-ux-pro-max] [--enable-workflows] [--dry-run] [--scope user|project|local]
#
# What it does (each step is skipped when already done):
#   1. preflight: claude >= 2.1.269, python3 >= 3.9, git, node/npx (Playwright MCP)
#   2. backup ~/.claude/settings.json (timestamped, under ~/.claude/backups/devpipeline-<ts>/)
#   3. validate this repository as a plugin + marketplace
#   4. register the local marketplace "devpipeline-local" (this repo)        -> claude plugin marketplace add
#   5. install plugin devpipeline@devpipeline-local                          -> claude plugin install
#   6. (default) install the third-party UI UX Pro Max skill from its official
#      marketplace nextlevelbuilder/ui-ux-pro-max-skill (reviewed version: 2.15.0)
#   7. record every change in ~/.local/state/devpipeline/install-log.jsonl and run doctor
# It never edits settings files directly, never overwrites unrelated files, never pushes,
# never enables bypassPermissions. Restart Claude Code (or /reload-plugins) afterwards.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAUDE="${CLAUDE_BIN:-claude}"
SCOPE="user"
DRY=0
WITH_UX=1
ENABLE_WF=0
MARKET="devpipeline-local"
PLUGIN="devpipeline@${MARKET}"
UX_MARKET_SRC="nextlevelbuilder/ui-ux-pro-max-skill"
UX_MARKET="ui-ux-pro-max-skill"
UX_PLUGIN="ui-ux-pro-max@${UX_MARKET}"
STATE_DIR="${DEVPIPELINE_STATE_DIR:-$HOME/.local/state/devpipeline}"
LOG="$STATE_DIR/install-log.jsonl"
SETTINGS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"

while [ $# -gt 0 ]; do
  case "$1" in
    --without-ui-ux-pro-max) WITH_UX=0 ;;
    --with-ui-ux-pro-max) WITH_UX=1 ;;
    --dry-run) DRY=1 ;;
    --enable-workflows) ENABLE_WF=1 ;;
    --scope) SCOPE="$2"; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '\033[1m[devpipeline]\033[0m %s\n' "$*"; }
record() {
  mkdir -p "$STATE_DIR"
  python3 - "$LOG" "$1" "$2" <<'PY'
import json, sys, datetime
path, action, detail = sys.argv[1:4]
with open(path, "a") as fh:
    fh.write(json.dumps({"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "action": action, "detail": detail}) + "\n")
PY
}
run() {
  if [ "$DRY" = 1 ]; then say "dry-run: $*"; return 0; fi
  "$@"
}
json_has() {  # json_has <json> <python-expression over data>
  python3 -c "import json,sys; data=json.loads(sys.argv[1] or '[]'); sys.exit(0 if ($2) else 1)" "$1"
}

# 1. preflight -----------------------------------------------------------------
command -v "$CLAUDE" >/dev/null || { echo "claude CLI not found" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }
command -v git >/dev/null || { echo "git not found" >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "python >= 3.9 required" >&2; exit 1; }
VER="$("$CLAUDE" --version 2>/dev/null | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
python3 - "$VER" <<'PY' || { echo "Claude Code >= 2.1.269 required (found $VER). Update with 'claude update' yourself; DevPipeline does not upgrade it." >&2; exit 1; }
import sys
v = tuple(int(x) for x in (sys.argv[1] or "0.0.0").split("."))
sys.exit(0 if v >= (2, 1, 269) else 1)
PY
command -v npx >/dev/null || say "warning: npx not found — Playwright MCP (browser QA) will be unavailable"
say "Claude Code $VER, python $(python3 -c 'import platform;print(platform.python_version())'), root $ROOT"

# 2. backup -----------------------------------------------------------------------
if [ -f "$SETTINGS" ]; then
  TS="$(date -u +%Y%m%dT%H%M%SZ)"
  BK="$(dirname "$SETTINGS")/backups/devpipeline-$TS"
  run mkdir -p "$BK"
  run cp -p "$SETTINGS" "$BK/settings.json"
  [ "$DRY" = 1 ] || record backup "$BK/settings.json"
  say "backed up settings.json -> $BK/settings.json"
fi

# 3. validate ---------------------------------------------------------------------
python3 "$ROOT/scripts/build_workflows.py" --check >/dev/null || { echo "workflows/ out of sync: run scripts/build_workflows.py" >&2; exit 1; }
python3 "$ROOT/scripts/validate_config.py" >/dev/null || { python3 "$ROOT/scripts/validate_config.py"; exit 1; }
"$CLAUDE" plugin validate "$ROOT" >/dev/null || { "$CLAUDE" plugin validate "$ROOT"; exit 1; }
say "plugin, marketplace and configuration validated"

# 4. marketplace --------------------------------------------------------------------
MKTS="$("$CLAUDE" plugin marketplace list --json 2>/dev/null || echo '[]')"
if json_has "$MKTS" "any(m.get('name') == '$MARKET' for m in data)"; then
  say "marketplace $MARKET already registered — refreshing"
  run "$CLAUDE" plugin marketplace update "$MARKET" >/dev/null
else
  run "$CLAUDE" plugin marketplace add "$ROOT"
  [ "$DRY" = 1 ] || record marketplace-add "$MARKET=$ROOT"
fi

# 5. plugin ---------------------------------------------------------------------------
PLUGS="$("$CLAUDE" plugin list --json 2>/dev/null || echo '[]')"
if json_has "$PLUGS" "any(p.get('id') == '$PLUGIN' for p in data)"; then
  say "$PLUGIN already installed — updating to the current source"
  run "$CLAUDE" plugin update "$PLUGIN" >/dev/null || true
else
  run "$CLAUDE" plugin install "$PLUGIN" --scope "$SCOPE"
  [ "$DRY" = 1 ] || record plugin-install "$PLUGIN scope=$SCOPE"
fi

# 6. UI UX Pro Max (third-party, official marketplace) -------------------------------
if [ "$WITH_UX" = 1 ]; then
  if json_has "$PLUGS" "any(p.get('id', '').startswith('ui-ux-pro-max@') for p in data)"; then
    say "UI UX Pro Max already installed"
  else
    say "installing UI UX Pro Max from its official marketplace ($UX_MARKET_SRC, MIT; reviewed 2.15.0: skills only, no hooks/MCP)"
    if ! json_has "$MKTS" "any(m.get('name') == '$UX_MARKET' for m in data)"; then
      run "$CLAUDE" plugin marketplace add "$UX_MARKET_SRC"
      [ "$DRY" = 1 ] || record marketplace-add "$UX_MARKET=$UX_MARKET_SRC (installed-by-devpipeline)"
    fi
    run "$CLAUDE" plugin install "$UX_PLUGIN" --scope "$SCOPE"
    [ "$DRY" = 1 ] || record plugin-install "$UX_PLUGIN scope=$SCOPE (installed-by-devpipeline)"
  fi
fi

# 6b. optional: turn on Claude Code dynamic workflows for this user (explicit opt-in only) --------------
if [ "$ENABLE_WF" = 1 ]; then
  if [ "$DRY" = 1 ]; then
    say "dry-run: set enableWorkflows=true in $SETTINGS (merged, other keys preserved, backup above)"
  else
    python3 - "$SETTINGS" <<'MERGE'
import json, os, sys, tempfile
path = sys.argv[1]
data = json.load(open(path)) if os.path.exists(path) else {}
if data.get("enableWorkflows") is not True:
    data["enableWorkflows"] = True
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    print("enableWorkflows=true written (all other settings preserved)")
MERGE
    record settings-merge "enableWorkflows=true in $SETTINGS"
  fi
fi

# 7. verify -----------------------------------------------------------------------------
if [ "$DRY" = 0 ]; then
  python3 "$ROOT/scripts/doctor.py" || true
  say "done. Restart Claude Code (or run /reload-plugins) so agents, skills, hooks, MCP servers and workflows load."
  say "commands: /devpipeline:build-project · improve-project · audit-project · repair-project · project-status · resume-project · project-doctor"
fi
