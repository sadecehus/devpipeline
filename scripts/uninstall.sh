#!/usr/bin/env bash
# DevPipeline uninstaller — removes only what DevPipeline installed.
#
#   scripts/uninstall.sh [--keep-data] [--remove-ui-ux-pro-max] [--dry-run]
#
# Removes: plugin devpipeline@devpipeline-local and the devpipeline-local marketplace.
# UI UX Pro Max is removed only with --remove-ui-ux-pro-max AND only if the install log
# shows DevPipeline installed it. Project directories (.devpipeline/ state, worktrees,
# integration branches) are never deleted. settings.json is backed up first.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAUDE="${CLAUDE_BIN:-claude}"
STATE_DIR="${DEVPIPELINE_STATE_DIR:-$HOME/.local/state/devpipeline}"
LOG="$STATE_DIR/install-log.jsonl"
SETTINGS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
DRY=0; KEEP=""; RM_UX=0
while [ $# -gt 0 ]; do
  case "$1" in
    --keep-data) KEEP="--keep-data" ;;
    --remove-ui-ux-pro-max) RM_UX=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac; shift
done
say() { printf '\033[1m[devpipeline]\033[0m %s\n' "$*"; }
run() { if [ "$DRY" = 1 ]; then say "dry-run: $*"; else "$@"; fi; }
has() { python3 -c "import json,sys; d=json.loads(sys.argv[1] or '[]'); sys.exit(0 if ($2) else 1)" "$1"; }

if [ -f "$SETTINGS" ]; then
  BK="$(dirname "$SETTINGS")/backups/devpipeline-uninstall-$(date -u +%Y%m%dT%H%M%SZ)"
  run mkdir -p "$BK"; run cp -p "$SETTINGS" "$BK/settings.json"; say "backed up settings.json -> $BK"
fi
PLUGS="$("$CLAUDE" plugin list --json 2>/dev/null || echo '[]')"
MKTS="$("$CLAUDE" plugin marketplace list --json 2>/dev/null || echo '[]')"
if has "$PLUGS" "any(p.get('id') == 'devpipeline@devpipeline-local' for p in d)"; then
  run "$CLAUDE" plugin uninstall devpipeline@devpipeline-local $KEEP
else
  say "plugin devpipeline not installed"
fi
if has "$MKTS" "any(m.get('name') == 'devpipeline-local' for m in d)"; then
  run "$CLAUDE" plugin marketplace remove devpipeline-local
fi
if [ "$RM_UX" = 1 ]; then
  if [ -f "$LOG" ] && grep -q 'ui-ux-pro-max@ui-ux-pro-max-skill.*installed-by-devpipeline' "$LOG"; then
    run "$CLAUDE" plugin uninstall ui-ux-pro-max@ui-ux-pro-max-skill || true
    grep -q 'ui-ux-pro-max-skill=.*installed-by-devpipeline' "$LOG" && run "$CLAUDE" plugin marketplace remove ui-ux-pro-max-skill || true
  else
    say "UI UX Pro Max was not installed by DevPipeline — leaving it in place"
  fi
fi
if [ -f "$LOG" ] && grep -q '"settings-merge".*enableWorkflows=true' "$LOG"; then
  if [ "$DRY" = 1 ]; then say "dry-run: remove enableWorkflows (set by install.sh --enable-workflows) from $SETTINGS"; else
    python3 - "$SETTINGS" <<'MERGE'
import json, os, sys, tempfile
path = sys.argv[1]
if os.path.exists(path):
    data = json.load(open(path))
    if data.pop("enableWorkflows", None) is not None:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        print("removed enableWorkflows (it was set by DevPipeline)")
MERGE
  fi
fi
say "uninstalled. Project .devpipeline/ directories and devpipeline/* branches were left untouched."
say "framework source remains at $ROOT (delete it manually if you no longer need it). Restart Claude Code."
