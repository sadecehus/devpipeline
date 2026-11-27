#!/usr/bin/env bash
# DevPipeline updater — re-validates the source, regenerates workflows, refreshes the
# local marketplace and updates the installed plugin copy. Never pulls or pushes git
# remotes on its own (run `git pull` yourself if the source has an upstream).
#
#   scripts/update.sh [--dry-run]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAUDE="${CLAUDE_BIN:-claude}"
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
say() { printf '\033[1m[devpipeline]\033[0m %s\n' "$*"; }
run() { if [ "$DRY" = 1 ]; then say "dry-run: $*"; else "$@"; fi; }
python3 "$ROOT/scripts/sync_agent_contract.py" --check
python3 "$ROOT/scripts/build_workflows.py" --check || { say "regenerating workflows"; run python3 "$ROOT/scripts/build_workflows.py"; }
python3 "$ROOT/scripts/validate_config.py"
python3 -m unittest discover -s "$ROOT/tests/unit" -q
"$CLAUDE" plugin validate "$ROOT" >/dev/null
run "$CLAUDE" plugin marketplace update devpipeline-local
run "$CLAUDE" plugin update devpipeline@devpipeline-local
say "updated to $(cat "$ROOT/VERSION"). Restart Claude Code to load the new version."
