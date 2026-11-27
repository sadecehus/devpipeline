#!/usr/bin/env python3
"""Generate the final run report (Markdown) from persisted evidence only.

  report_generator.py --project DIR --run ID

Written for every run (completed, failed, blocked). The word COMPLETED is only
used when the run's final status is COMPLETED (which the state manager allows
only after a passing deterministic gate for the current fingerprint).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dpcore.util import DPError, atomic_write_text, emit, now_iso, read_json  # noqa: E402
from redact_secrets import redact  # noqa: E402
import state_manager as sm  # noqa: E402


def _opt(path: Path):
    try:
        return read_json(path) if path.exists() else None
    except DPError:
        return None


def classify(req: dict | None, tasks: list[dict], final_review: dict | None, gate_fp: str | None) -> list[dict]:
    rows = []
    if not req:
        return rows
    matrix = {}
    fresh = final_review and gate_fp and final_review.get("source_fingerprint") == gate_fp
    if final_review:
        matrix = {r["ac_id"]: r["status"] for r in final_review.get("acceptance_matrix", [])}
    for r in req["requirements"]:
        ts = [t for t in tasks if r["id"] in t.get("requirements", [])]
        acs = [ac["id"] for ac in r["acceptance_criteria"]]
        if any(t["status"] == "BLOCKED" for t in ts) and not any(t["status"] == "COMPLETED" for t in ts):
            state = "BLOCKED"
        elif not any(t["status"] == "COMPLETED" for t in ts):
            state = "MISSING"
        elif fresh and all(matrix.get(a) == "VERIFIED" for a in acs):
            state = "VERIFIED"
        else:
            state = "IMPLEMENTED_UNVERIFIED"
        rows.append({"id": r["id"], "title": r["title"], "priority": r["priority"], "state": state,
                     "acs": [(a, matrix.get(a, "—")) for a in acs]})
    return rows


def generate(project: Path, run_id: str) -> str:
    project = Path(project).resolve()
    rd = sm.run_dir(project, run_id)
    dp = sm.dp_dir(project)
    m = sm.load_manifest(project, run_id)
    req = _opt(dp / "requirements.json")
    arch = _opt(dp / "architecture.json")
    tasks = (_opt(rd / "tasks.json") or {}).get("tasks", [])
    gate = _opt(rd / m["gate_results"][-1]["path"]) if m["gate_results"] else None
    fr = _opt(rd / "reviews" / "final_review.json")
    cr = _opt(rd / "reviews" / "code_review.json")
    reports = {f.stem: _opt(f) for f in sorted((rd / "test-reports").glob("*.json"))}
    issues = [_opt(f) for f in sorted((rd / "issues").glob("ISSUE-*.json"))]
    status = m.get("final_status") or m["phase"]
    rows = classify(req, tasks, fr, gate.get("code_fingerprint") if gate else None)
    L: list[str] = []
    title = (req or {}).get("project_title") or Path(m["project_dir"]).name
    L += [f"# DevPipeline run report — {title}", "",
          f"- **Run:** `{run_id}` ({m['mode']} mode, {m['project_mode']} project, profile `{m['config']['profile']}`)",
          f"- **Final status:** **{status}**" + (f" — {m.get('stop_reason')}" if m.get("stop_reason") and status != "COMPLETED" else ""),
          f"- **Started / ended:** {m['started_at']} / {m.get('ended_at') or 'not ended'}",
          f"- **Framework version:** {m['framework_version']}",
          f"- **Source revision:** base `{m['source'].get('base_revision')}`, integration `{m['source'].get('integration_head')}` "
          f"(branch `{m['source'].get('integration_branch', '—')}`), code fingerprint `{gate.get('code_fingerprint') if gate else '—'}`",
          f"- **Repair iterations:** {m['counters']['repair_iterations']} · agent calls {m['counters']['agent_calls']} · operator calls {m['counters']['operator_calls']}",
          "- **Usage/cost:** not observable from the workflow runtime; no token or cost figures are reported.", ""]
    if m["config"]["profile"] == "fast":
        L += ["> Fast profile: prototype-level verification only (reduced review stages). This result is **not** a production-readiness claim.", ""]
    if status != "COMPLETED":
        L += ["> This run is **not** complete. Mandatory checks remain unresolved or unverified; see the quality gate and remaining issues below.", ""]
    L += ["## Original request", "", "```text", redact(m["original_request"]).strip()[:4000], "```", ""]
    if arch:
        L += ["## Technology stack & architecture", ""]
        L += [f"- **{k}:** {v}" for k, v in arch.get("stack", {}).items() if v]
        L += [f"- Project type: {arch.get('project_type')}; style: {arch.get('style', '—')}", ""]
        for c in arch.get("components", []):
            L.append(f"- `{c['name']}` ({c['owner_agent']}): {c['responsibility']} — {', '.join(c['paths'])}")
        L.append("")
    L += ["## Agents", ""]
    for d in m.get("agent_decisions", []):
        if d["decision"] != "not activated":
            L.append(f"- **{d['agent']}** — {d['decision']}: {d['why']}")
    L.append("")
    L += ["## Requirements and acceptance criteria", "",
          "| Requirement | Priority | State | Acceptance criteria |", "|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['id']} {r['title']} | {r['priority']} | {r['state']} | " + ", ".join(f"{a}:{s}" for a, s in r["acs"]) + " |")
    if not rows:
        L.append("| — | — | — | no requirements recorded |")
    L += ["", "States: VERIFIED = final reviewer verified all ACs on the current revision; IMPLEMENTED_UNVERIFIED = a completed task covers it but verification is missing/stale; MISSING; BLOCKED.", ""]
    L += ["## Implemented features", ""]
    L += [f"- **{r['id']} {r['title']}** — {r['state'].replace('_', ' ').lower()}" for r in rows] or ["- none recorded"]
    L += ["", "## Tasks", "", "| Task | Agent | Status | Attempts | Note |", "|---|---|---|---|---|"]
    for t in tasks:
        L.append(f"| {t['id']} {t['title']} | {t['agent']} | {t['status']} | {t['attempt']} | {(t.get('failure_reason') or '')[:120]} |")
    L.append("")
    L += ["## Quality gate", ""]
    if gate:
        L += [f"Gate #{gate['seq']} at {gate['created_at']}: **{gate['overall']}**", "", "| Gate | Mandatory | Status | Reason |", "|---|---|---|---|"]
        for g in gate["gates"]:
            L.append(f"| {g['id']} | {'yes' if g['mandatory'] else 'no'} | {g['status']} | {str(g.get('reason', ''))[:160]} |")
    else:
        L.append("No quality gate was evaluated in this run.")
    L.append("")
    integ = _opt(rd / "agent-results" / "INTEGRATION.json")
    L += ["## Integration results", ""]
    if m.get("source", {}).get("integration_head"):
        L.append(f"- Integration branch `{m['source'].get('integration_branch')}` at `{m['source'].get('integration_head')}`; isolation `{m.get('isolation')}`")
    if integ:
        L.append(f"- Integration engineer: {integ.get('status')} — {redact(integ.get('summary', ''))[:300]}")
    for v in m.get("ownership_violations", []):
        L.append(f"- Ownership violation ({v['scope']}, {'resolved' if v.get('resolved') else 'open'}): {', '.join(v['files'][:8])}")
    L += ["", "## Test, build and QA evidence", ""]
    for name, rep in reports.items():
        if not rep:
            continue
        L.append(f"### {name} — {rep.get('status')}{' (stale)' if rep.get('stale') else ''}")
        for c in rep.get("checks", [])[:60]:
            counts = ""
            if c.get("tests_total") is not None:
                counts = f" — tests {c.get('tests_passed')}/{c.get('tests_total')} passed, {c.get('tests_failed')} failed, {c.get('tests_skipped')} skipped"
            L.append(f"- `{c['name']}` {c['status']}{counts}" + (f" — {c['reason']}" if c.get("reason") else "")
                     + (f" — log `{c['log_path']}`" if c.get("log_path") else ""))
        L.append("")
    for label, rv in (("Code review", cr), ("Final review", fr)):
        if rv:
            L += [f"### {label}: {rv['status']} (approved={rv['approved']})", "", redact(rv.get("summary", "")), ""]
    open_i = [i for i in issues if i and i["status"] in ("OPEN", "IN_REPAIR")]
    pre = [i for i in issues if i and i["status"] == "PRE_EXISTING"]
    resolved = [i for i in issues if i and i["status"] == "RESOLVED"]
    sec = [i for i in issues if i and i["category"] in ("security", "ai_security")]
    L += ["## Security findings", ""]
    L += [f"- {i['id']} [{i['severity']}] {i['status']}: {i.get('title') or i['description'][:100]}" for i in sec] or ["- none recorded"]
    L += ["", "## Remaining issues", ""]
    L += [f"- {i['id']} [{i['severity']}{', blocking' if i['blocking'] else ''}] → {i['assigned_agent']}: {i.get('title') or i['description'][:140]}" for i in open_i] or ["- none"]
    if pre:
        L += ["", "Pre-existing failures (present before this run, not introduced by it):"]
        L += [f"- {i['id']}: {i.get('title') or i['description'][:140]}" for i in pre]
    L += ["", f"Resolved during this run: {len(resolved)} issue(s).", ""]
    if req:
        L += ["## Assumptions", ""] + [f"- {a}" for a in req.get("assumptions", [])] + [""]
        L += ["## Non-goals / future work", ""] + [f"- {a}" for a in req.get("non_goals", []) + req.get("future_improvements", [])] + [""]
    ai = reports.get("ai_eval")
    if ai:
        live = [c for c in ai.get("checks", []) if c["name"].startswith("live:")]
        L += ["## AI evaluation", "", f"Deterministic suite status: {ai.get('status')}.",
              "Live model evaluation: " + (", ".join(f"{c['name']}={c['status']}" for c in live) or "not part of the report") +
              ". Mock-based results do not establish real model quality.", ""]
    L += ["## Known limitations", ""]
    lim = [f"- {k}: {c.get('status')} — {c.get('reason', '')}" for name, rep in reports.items() if rep
           for c in rep.get("checks", []) for k in [c["name"]] if c.get("status") in ("NOT_RUN", "SKIPPED", "BLOCKED")]
    lim += [f"- gate {g['id']}: {g['status']} ({g.get('reason', '')})" for g in (gate or {}).get("gates", []) if not g["mandatory"] and g["status"] in ("NOT_RUN", "FAIL")]
    lim += ["- Token/cost usage not measured; performance figures (if any) are local lab measurements."]
    L += lim[:40] + [""]
    fin = m.get("source", {}).get("finalize", {})
    integ = m["source"].get("integration_worktree")
    L += ["## Local startup & tests", ""]
    if arch and arch.get("app_start"):
        L.append(f"- Start: `{arch['app_start'].get('command')}` (cwd `{arch['app_start'].get('cwd', '.')}`) → {arch['app_start'].get('url', '')}")
    L += [f"- Integrated code: `{integ}`", "- Re-run all discovered checks: `python3 <plugin>/scripts/run_checks.py --discover --dir <project>` then the listed commands.",
          "- See README.md in the project for the documented commands.", ""]
    L += ["## Artifacts", "", f"- Run directory: `{rd}`", f"- Events: `{rd / 'events.jsonl'}`", f"- Gate results: `{rd / 'gates'}`",
          f"- Agent results: `{rd / 'agent-results'}`", f"- Checkpoints: `{dp / 'checkpoints' / run_id}`", ""]
    if fin.get("merge_hint") and not fin.get("fast_forwarded"):
        L += [f"- Integration branch kept: `{fin.get('integration_branch')}` — merge when satisfied: `{fin['merge_hint']}`", ""]
    L += ["## Recommended next steps", ""]
    if status == "COMPLETED":
        L += ["- Review the integration branch / diff, then run the app locally.", "- Consider the future improvements listed above."]
    else:
        L += ["- Inspect remaining blocking issues and the failing gates above.",
              "- Resume with `/devpipeline:resume-project` after resolving blockers (credentials, approvals, environment)."]
    L += ["", f"_Generated {now_iso()} from persisted evidence by report_generator.py._", ""]
    out = rd / "final-report.md"
    atomic_write_text(out, "\n".join(L))
    return str(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True)
    ap.add_argument("--run", required=True)
    a = ap.parse_args(argv)
    try:
        return emit({"ok": True, "report": generate(Path(a.project), a.run)})
    except DPError as exc:
        return emit({"ok": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
