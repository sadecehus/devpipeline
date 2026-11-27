#!/usr/bin/env python3
"""Assemble workflows/*.js from scripts/workflow_src/engine.js.

The Claude Code workflow runtime forbids imports, so each workflow file must be
self-contained. To keep ONE source of truth, the shared engine lives in
scripts/workflow_src/engine.js and this script prepends the per-mode `meta`
header and appends the entry call.

  build_workflows.py          regenerate workflows/
  build_workflows.py --check  exit 1 when workflows/ differs from the source
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "scripts" / "workflow_src" / "engine.js"
PHASES = "[{ title: 'INITIALIZING' }, { title: 'ANALYZING' }, { title: 'PLANNING' }, { title: 'DESIGNING' }, { title: 'DEVELOPING' }, { title: 'INTEGRATING' }, { title: 'TESTING' }, { title: 'REVIEWING' }, { title: 'REPAIRING' }, { title: 'DOCUMENTING' }]"
ENTRIES = {
    "build-project.js": ("pipeline-build", "build",
                         "DevPipeline: build a new project from a natural-language idea with a dynamic multi-agent team",
                         "Only via /devpipeline:build-project, which prepares the run and passes args"),
    "improve-project.js": ("pipeline-improve", "improve",
                           "DevPipeline: add or change features in an existing repository with baseline-aware verification",
                           "Only via /devpipeline:improve-project, which prepares the run and passes args"),
    "audit-project.js": ("pipeline-audit", "audit",
                         "DevPipeline: read-only audit of an existing project (QA, security, AI evaluation, code review)",
                         "Only via /devpipeline:audit-project, which prepares the run and passes args"),
    "repair-project.js": ("pipeline-repair", "repair",
                          "DevPipeline: repair user-approved findings from an earlier run, then re-verify",
                          "Only via /devpipeline:repair-project, which prepares the run and passes args"),
}


def render(name: str, mode: str, desc: str, when: str) -> str:
    meta = (
        "export const meta = {\n"
        f"  name: '{name}',\n"
        f"  description: '{desc}',\n"
        f"  whenToUse: '{when}',\n"
        f"  phases: {PHASES},\n"
        "}\n"
    )
    return meta + "\n" + ENGINE.read_text() + f"\nreturn await runPipeline('{mode}')\n"


SMOKE_ENTRY = """
// Real-runtime smoke test (tests/smoke/runtime-smoke.js — not shipped as a plugin workflow).
async function runSmoke() {
  await loadContext()
  const t = await to('ANALYZING', 'runtime smoke test')
  const req = await mustOp('validate-requirements')
  await to('PLANNING', 'runtime smoke: large relay')
  const plan = await mustOp('validate-plan')
  const ctx2 = await mustOp('context')
  const r = await specialist('orchestrator', 'Runtime smoke test of DevPipeline agent resolution. Do not use any tools. Return status COMPLETED with summary exactly "agentType resolved".', { label: 'smoke:orchestrator' })
  const out = await finalize('CANCELLED', 'runtime smoke test finished')
  return { context_phase_seen: CTX.phase, transition_ok: t.ok, requirements_valid: req.valid, plan_valid: plan.valid, plan_tasks: plan.tasks.length, plan_agents: plan.selected_agents.length, context_tasks: ctx2.tasks.length, specialist: r, final_status: out.final_status, operator_calls: counters.operator_calls, agent_calls: counters.agent_calls }
}
return await runSmoke()
"""


def render_smoke() -> str:
    meta = ("export const meta = {\n  name: 'devpipeline-runtime-smoke',\n"
            "  description: 'DevPipeline real-runtime smoke test (operator relay, checksum, agentType resolution)',\n}\n")
    return meta + "\n" + ENGINE.read_text() + SMOKE_ENTRY


def main(argv: list[str]) -> int:
    drift = []
    smoke = ROOT / "tests" / "smoke" / "runtime-smoke.js"
    if not smoke.exists() or smoke.read_text() != render_smoke():
        drift.append("tests/smoke/runtime-smoke.js")
        if "--check" not in argv:
            smoke.write_text(render_smoke())
    for fname, (name, mode, desc, when) in ENTRIES.items():
        target = ROOT / "workflows" / fname
        text = render(name, mode, desc, when)
        if not target.exists() or target.read_text() != text:
            drift.append(fname)
            if "--check" not in argv:
                target.write_text(text)
    if "--check" in argv:
        print("workflows in sync" if not drift else "workflow drift: " + ", ".join(drift))
        return 1 if drift else 0
    print(f"generated {len(ENTRIES)} workflows ({len(drift)} changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
