#!/usr/bin/env bash
# Real Claude Code runtime smoke test. Consumes a small amount of usage
# (3 Haiku relay calls + 1 Sonnet agent call + the Sonnet main turn).
#
# Verifies: the Workflow runtime executes the generated engine; pipeline-operator relay
# with checksum verification; plugin agent resolution via agentType; structured output;
# state transitions/checkpoints; plugin hooks receive agent_type "devpipeline:*" for
# workflow agents (events recorded with source=hook).
#
#   tests/smoke/run_runtime_smoke.sh
# Uses --settings '{"enableWorkflows": true}' for this invocation only (user settings untouched).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d)"
export DEVPIPELINE_STATE_DIR="$WORK/state"
python3 "$ROOT/scripts/build_workflows.py" >/dev/null
ARGS="$(python3 "$ROOT/scripts/devpipeline.py" prepare --project "$WORK/project" --mode build --new --profile fast \
  --request "runtime smoke" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["workflow_args"]))')"
# planning artifacts with 12 tasks so the relay carries realistic, large JSON (validate-plan / context)
python3 - "$WORK/project/.devpipeline" <<'PLAN'
import json, sys
from pathlib import Path
d = Path(sys.argv[1])
reqs = [{"id": f"REQ-{i:03d}", "title": f"Feature {i}", "priority": "must", "type": "functional", "description": f"feature {i} with unicode çğü",
         "acceptance_criteria": [{"id": f"AC-{i:03d}", "description": f"feature {i} behaves as specified", "verification_method": "unit"}]} for i in range(1, 13)]
(d / "requirements.json").write_text(json.dumps({"project_title": "Smoke", "summary": "relay test", "requirements": reqs, "assumptions": [], "non_goals": [],
                                                  "features": {"backend": True, "frontend": True, "database": True, "ai": True}}))
agents = ["backend-engineer", "frontend-engineer", "database-engineer", "ai-engineer"]
tasks = [{"id": f"TASK-{i:03d}", "agent": agents[i % 4], "title": f"Implement feature {i} (çğü)", "requirements": [f"REQ-{i:03d}"],
          "dependencies": [f"TASK-{i - 4:03d}"] if i > 4 else [], "owned_paths": [f"mod{i}/"], "acceptance_criteria": [f"AC-{i:03d}"],
          "status": "PENDING", "attempt": 0} for i in range(1, 13)]
(d / "tasks.json").write_text(json.dumps({"tasks": tasks}))
(d / "architecture.json").write_text(json.dumps({"project_type": "fullstack", "stack": {"backend": "x"}, "features": {"backend": True},
    "components": [], "ownership": [{"agent": a, "paths": [t["owned_paths"][0] for t in tasks if t["agent"] == a]} for a in agents],
    "required_agents": agents, "test_strategy": {}, "adrs": []}))
PLAN
OUT="$(cd "$ROOT" && claude -p --model sonnet --settings '{"enableWorkflows": true}' \
  --allowedTools "Workflow" "Bash(python3 *)" "Read" -- \
  "Use the Workflow tool exactly once with scriptPath \"$ROOT/tests/smoke/runtime-smoke.js\" and args set to this JSON object (as an object, not a string): $ARGS . When the workflow returns, print its return value as compact JSON and nothing else. If the Workflow tool is unavailable, say exactly why.")"
echo "$OUT"
EVENTS="$(cat "$WORK"/project/.devpipeline/runs/*/events.jsonl)"
python3 - "$OUT" "$EVENTS" <<'CHECK'
import json, re, sys
out, events = sys.argv[1], sys.argv[2]
m = re.search(r"\{.*\}", out, re.S)
res = json.loads(m.group(0)) if m else {}
checks = {
    "workflow ran and finalized": res.get("final_status") == "CANCELLED",
    "operator relay verified (>= 6 ops incl. large outputs)": res.get("operator_calls", 0) >= 6,
    "large relays exact (12 tasks via validate-plan and context)": res.get("plan_valid") is True and res.get("plan_tasks") == 12 and res.get("context_tasks") == 12,
    "plugin agent resolved via agentType": (res.get("specialist") or {}).get("status") == "COMPLETED",
    "hooks received agent_type for workflow agents": '"source": "hook"' in events and "orchestrator" in events,
}
for name, ok in checks.items():
    print(("PASS " if ok else "FAIL ") + name)
sys.exit(0 if all(checks.values()) else 1)
CHECK
