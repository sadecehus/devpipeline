#!/usr/bin/env python3
"""Deterministic quality gate.

Reads *evidence files* (check reports with exit codes and logs, QA reports,
review verdicts, task states) — never an agent's claim in conversation — and
decides PASS/FAIL per gate. A mandatory gate passes only with status PASS and
valid evidence for the *current* code fingerprint; NOT_APPLICABLE is decided by
rules from config/quality-gates.json, not by agents. SKIPPED, NOT_RUN, BLOCKED
and ERROR never count as PASS. An LLM approval cannot override a failed check.

  quality_gate.py --project DIR --run ID [--final]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dpcore import config as cfgmod  # noqa: E402
from dpcore import gitutil  # noqa: E402
from dpcore import issues as issuemod  # noqa: E402
from dpcore.jsonschema_lite import validate_named  # noqa: E402
from dpcore.util import DPError, atomic_write_json, emit, now_iso, read_json  # noqa: E402
from redact_secrets import find as find_secrets  # noqa: E402
import state_manager as sm  # noqa: E402

VERIFICATION_TO_GATE = {"e2e": "e2e_tests", "accessibility": "accessibility", "performance": "performance",
                        "ai_eval": "ai_evaluation", "security_scan": "security_review", "integration": "integration_tests",
                        "api": "api_contract_tests", "contract": "api_contract_tests", "unit": "unit_tests"}
CHECK_KIND_TO_CATEGORY = {"install": "build", "build": "build", "typecheck": "code_quality", "lint": "code_quality",
                          "unit": "test", "integration": "test", "api": "api", "contract": "contract", "e2e": "functional",
                          "accessibility": "accessibility", "security": "security"}


def _latest(files: list[Path]) -> list[tuple[Path, dict]]:  # noqa: D401
    """Order reports oldest -> newest by file modification time (created_at has 1 s resolution)."""
    out = []
    for f in files:
        try:
            out.append((f, read_json(f)))
        except DPError:
            out.append((f, {"_invalid": True}))
    return sorted(out, key=lambda x: (x[0].stat().st_mtime_ns, x[0].name))


class Context:
    def __init__(self, project: Path, run_id: str):
        self.project = Path(project).resolve()
        self.run_id = run_id
        self.rd = sm.run_dir(self.project, run_id)
        self.m = sm.load_manifest(self.project, run_id)
        self.cfg = self.m["config"]
        self.mode = self.m["mode"]
        self.features = self.m.get("features") or {}
        self.selected = {a["agent"] for a in self.m.get("selected_agents", [])}
        self.wt = Path(self.m.get("source", {}).get("integration_worktree") or self.project)
        if self.mode in ("build", "improve") and self.wt.exists():
            from dpcore.selection import detect_features, merge_features
            self.features = merge_features(self.features, detect_features(self.wt))
        fps = sm.fingerprints(self.project, self.m)
        self.code_fp, self.prod_fp, self.docs_fp = fps["code"], fps["prod"], fps["docs"]
        tasks_path = self.rd / "tasks.json"
        self.tasks = read_json(tasks_path)["tasks"] if tasks_path.exists() else []
        reports = _latest(sorted((self.rd / "test-reports").glob("*.json")))
        recorded = set(self.m.get("check_reports", []))  # written by run_checks via the coordinator only
        self.check_reports = [(f, r) for f, r in reports if r.get("kind") == "checks"
                              and f"test-reports/{f.name}" in recorded and r.get("agent") == "pipeline-operator"]
        self.baseline = next((r for f, r in reversed(reports) if r.get("kind") == "baseline"
                              and f"test-reports/{f.name}" in recorded), None)
        self.kind_reports: dict[str, tuple[Path, dict]] = {}
        qa = cfgmod.qa_kinds()
        for f, r in reports:
            kind = r.get("kind", "?")
            if kind in ("checks", "baseline"):
                continue
            if f.name != f"{kind}.json" or qa.get(r.get("agent")) != kind:
                continue  # a QA report counts only at its canonical path and from the agent that owns the kind
            self.kind_reports[kind] = (f, r)
        self.reviews = {}
        for f in sorted((self.rd / "reviews").glob("*.json")):
            try:
                rv = read_json(f)
                self.reviews[rv.get("kind", f.stem)] = (f, rv)
            except DPError:
                self.reviews[f.stem] = (f, {"_invalid": True})
        req_path = self.project / ".devpipeline" / "requirements.json"
        self.requirements = read_json(req_path) if req_path.exists() else None
        self.latest_checks = self.check_reports[-1][1] if self.check_reports else None
        self.detected_kinds = {c["kind"] for c in (self.latest_checks or {}).get("checks", []) if c.get("command") or c.get("status") == "FAIL"}

    def impl_count(self) -> int:
        reg = cfgmod.registry()
        return sum(1 for a in self.selected if reg["agents"].get(a, {}).get("role") in reg["implementation_roles"])


def applies(g: dict, ctx: Context) -> tuple[bool, str]:
    cond = g.get("applies", {})
    if "modes" in cond and ctx.mode not in cond["modes"]:
        return False, f"not applicable in {ctx.mode} mode"
    if cond.get("always"):
        return True, "always applies"
    keys = [k for k in cond if k != "modes"]
    if not keys:
        return True, "applies in this mode"
    for f in cond.get("any_feature", []):
        if ctx.features.get(f):
            return True, f"feature '{f}'"
    if ctx.cfg["profile"] in cond.get("profiles", []):
        return True, f"profile '{ctx.cfg['profile']}'"
    if cond.get("agent_selected") and cond["agent_selected"] in ctx.selected:
        return True, f"agent '{cond['agent_selected']}' active"
    for k in cond.get("check_kinds_detected", []):
        if k in ctx.detected_kinds:
            return True, f"'{k}' command discovered in project"
    need = cond.get("min_implementation_agents")
    if need is not None and ctx.impl_count() >= need:
        return True, "implementation agents active"
    return False, "applicability rules not met: " + ", ".join(f"{k}={cond[k]}" for k in keys)


def level_for(g: dict, ctx: Context) -> tuple[str, str]:
    level = ctx.cfg.get("gates", {}).get(g["id"], g["default"])
    why = f"profile/config level '{level}'"
    if ctx.requirements:
        for r in ctx.requirements["requirements"]:
            if r["priority"] != "must":
                continue
            for ac in r["acceptance_criteria"]:
                if VERIFICATION_TO_GATE.get(ac["verification_method"]) == g["id"] and level != "mandatory":
                    return "mandatory", f"required by {r['id']}/{ac['id']} ({ac['verification_method']})"
    return level, why


def _evidence_ok(check: dict) -> tuple[bool, str]:
    if check.get("evidence_type") == "executed" and check["status"] == "PASS":
        if check.get("exit_code") != 0:
            return False, "PASS without exit code 0"
        if not check.get("log_path") or not Path(check["log_path"]).exists():
            return False, "PASS without log evidence"
    return True, ""


def eval_checks(g: dict, ctx: Context) -> dict:
    rep = ctx.latest_checks
    if not rep:
        return {"status": "NOT_RUN", "reason": "no check report"}
    if rep.get("source_fingerprint") != ctx.code_fp:
        return {"status": "NOT_RUN", "reason": "check evidence is stale (code changed since checks ran)"}
    checks = [c for c in rep["checks"] if c["kind"] in g.get("kinds", [])]
    if g.get("names"):
        checks = [c for c in checks if c["name"] in g["names"]]
    if g.get("names_prefix"):
        checks = [c for c in checks if c["name"].startswith(g["names_prefix"])]
    elif g["id"] == "secret_scan":
        pass
    elif "security" in g.get("kinds", []):
        checks = [c for c in checks if c["name"] == "secret-scan"]
    if not checks:
        return {"status": "NOT_RUN", "reason": f"no {'/'.join(g.get('kinds', []))} command discovered or executed"}
    statuses = []
    notes = []
    for c in checks:
        st = c["status"]
        ok, why = _evidence_ok(c)
        if not ok:
            st, why = "ERROR", why
            notes.append(f"{c['name']}: {why}")
        if st == "PASS" and g.get("tests_required"):
            if c.get("tests_total") == 0:
                st = "FAIL"
                notes.append(f"{c['name']}: 0 tests executed")
            elif (c.get("tests_skipped") or 0) > 0 and not cfgmod.gates_config().get("allow_skipped_tests"):
                st = "SKIPPED"
                notes.append(f"{c['name']}: {c['tests_skipped']} skipped test(s) — skipped tests never count as passing")
            elif c.get("tests_total") is None:
                st = "NOT_RUN"
                notes.append(f"{c['name']}: exit 0 but no test count could be parsed — unverifiable, not a pass")
        if st not in ("PASS",):
            notes.append(f"{c['name']}: {st}" + (f" ({c.get('reason')})" if c.get("reason") else ""))
        statuses.append(st)
    order = ["FAIL", "ERROR", "BLOCKED", "SKIPPED", "NOT_RUN"]
    status = next((s for s in order if s in statuses), "PASS")
    return {"status": status, "reason": "; ".join(dict.fromkeys(notes)) or f"{len(checks)} check(s) passed",
            "evidence": [c.get("log_path", c["name"]) for c in checks], "checks": [c["name"] for c in checks]}


def eval_report(g: dict, ctx: Context, open_issues: list[dict]) -> dict:
    kind = g["report_kind"]
    if kind not in ctx.kind_reports:
        return {"status": "NOT_RUN", "reason": f"no {kind} report"}
    f, rep = ctx.kind_reports[kind]
    if rep.get("_invalid"):
        return {"status": "ERROR", "reason": f"{f.name} is not valid JSON"}
    errs = validate_named(rep, "test-report.schema.json")
    if errs:
        return {"status": "ERROR", "reason": f"{f.name} violates test-report schema: {errs[0]}"}
    if rep.get("source_fingerprint") != ctx.prod_fp:
        return {"status": "NOT_RUN", "reason": f"{kind} evidence is stale (production code changed after the report)"}
    if not rep["checks"]:
        return {"status": "ERROR", "reason": f"{kind} report contains no checks (no evidence)"}
    notes, statuses = [], []
    counted_pass = 0
    for c in rep["checks"]:
        st = c["status"]
        ok, why = _evidence_ok(c)
        if not ok:
            st = "ERROR"
            notes.append(f"{c['name']}: {why}")
        if kind == "ai_eval" and c["name"].startswith("live:") and st in ("NOT_RUN", "BLOCKED", "SKIPPED"):
            policy = ctx.cfg.get("live_ai_evaluation", "optional")
            if policy == "mandatory":
                st = "BLOCKED"
                notes.append(f"{c['name']}: live evaluation mandatory but not executed ({c.get('reason', 'no credentials/approval')})")
            else:
                notes.append(f"{c['name']}: live evaluation NOT_RUN (policy {policy}); mock results do not establish real model quality")
                continue
        statuses.append(st)  # agents cannot mark their own checks optional
        if st == "PASS" and c.get("evidence_type", "executed") in ("executed", "observed", "static_analysis"):
            counted_pass += 1
    for scanner in rep.get("scanners", []):
        if scanner.get("ran") is False and scanner.get("status") == "PASS":
            statuses.append("ERROR")
            notes.append(f"scanner {scanner['name']} claims PASS but did not run")
        elif scanner.get("ran") is False:
            notes.append(f"scanner {scanner['name']} did not run (NOT_RUN, not counted as a pass)")
    if counted_pass == 0:
        statuses.append("NOT_RUN")
        notes.append(f"{kind} report has no passing executed, observed or static-analysis check (mock/LLM-judged results are not evidence)")
    if kind == "security":
        sev = [i for i in open_issues if i["category"] in ("security", "ai_security") and i["severity"] in ("critical", "high")]
        if sev:
            statuses.append("FAIL")
            notes.append(f"{len(sev)} unresolved critical/high security finding(s): " + ", ".join(i["id"] for i in sev))
    if kind == "browser_qa" and not any(c.get("evidence_type") in ("executed", "observed") for c in rep["checks"]):
        statuses.append("ERROR")
        notes.append("browser QA has no executed/observed browser evidence")
    if rep["status"] != "PASS":
        statuses.append(rep["status"] if rep["status"] in ("FAIL", "ERROR", "BLOCKED", "SKIPPED", "NOT_RUN") else "FAIL")
    order = ["FAIL", "ERROR", "BLOCKED", "SKIPPED", "NOT_RUN"]
    status = next((s for s in order if s in statuses), "PASS")
    return {"status": status, "reason": "; ".join(notes) or f"{kind} report PASS", "evidence": [str(f)]}


def eval_review(g: dict, ctx: Context) -> dict:
    kind = g["review_kind"]
    if kind not in ctx.reviews:
        return {"status": "NOT_RUN", "reason": f"no {kind} verdict"}
    f, rv = ctx.reviews[kind]
    if rv.get("_invalid"):
        return {"status": "ERROR", "reason": f"{f.name} invalid JSON"}
    errs = validate_named(rv, "review.schema.json")
    if errs:
        return {"status": "ERROR", "reason": f"{f.name} violates review schema: {errs[0]}"}
    if rv["source_fingerprint"] != ctx.code_fp:
        return {"status": "NOT_RUN", "reason": f"{kind} reviewed a different revision (stale)"}
    if rv["approved"] and rv["status"] == "APPROVED" and not rv["blocking_issues"]:
        return {"status": "PASS", "reason": f"{kind} approved", "evidence": [str(f)]}
    return {"status": "FAIL", "reason": f"{kind}: {rv['status']} with {len(rv['blocking_issues'])} blocking issue(s)", "evidence": [str(f)]}


def eval_coverage(ctx: Context) -> dict:
    if not ctx.requirements:
        return {"status": "NOT_RUN", "reason": "no requirements.json"}
    done = {r for t in ctx.tasks if t["status"] == "COMPLETED" for r in t.get("requirements", [])}
    missing = [r["id"] for r in ctx.requirements["requirements"] if r["priority"] in ("must", "should") and r["id"] not in done]
    fr = ctx.reviews.get("final_review")
    if not fr or fr[1].get("_invalid"):
        return {"status": "NOT_RUN", "reason": "final review acceptance matrix missing" + (f"; uncovered: {missing}" if missing else "")}
    matrix = {row["ac_id"]: row["status"] for row in fr[1].get("acceptance_matrix", [])}
    unverified = [ac["id"] for r in ctx.requirements["requirements"] if r["priority"] == "must"
                  for ac in r["acceptance_criteria"] if matrix.get(ac["id"]) != "VERIFIED"]
    if fr[1].get("source_fingerprint") != ctx.code_fp:
        return {"status": "NOT_RUN", "reason": "acceptance matrix refers to a stale revision"}
    if missing or unverified:
        return {"status": "FAIL", "reason": f"requirements without completed task: {missing}; must ACs not VERIFIED: {unverified}"}
    return {"status": "PASS", "reason": "all must/should requirements implemented and must ACs verified"}


_CMD = re.compile(r"\b(npm run|pnpm run|pnpm|yarn)\s+([a-zA-Z0-9:_-]+)")


def eval_documentation(ctx: Context) -> dict:
    readme = ctx.wt / "README.md"
    if not readme.exists():
        return {"status": "FAIL", "reason": "README.md missing"}
    text = readme.read_text(errors="ignore")
    notes = []
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 25:
        notes.append(f"README too short ({len(lines)} non-empty lines)")
    low = text.lower()
    for label, words in (("installation/setup", ("install", "setup", "kurulum")), ("running", ("run", "start", "usage", "çalıştır")),
                         ("testing", ("test",))):
        if not any(w in low for w in words):
            notes.append(f"README lacks a {label} section")
    scripts = set()
    for pkg in [ctx.wt / "package.json"] + list(ctx.wt.glob("*/package.json")) + list(ctx.wt.glob("*/*/package.json")):
        if "node_modules" in pkg.parts:
            continue
        try:
            import json
            scripts |= set((json.loads(pkg.read_text()).get("scripts") or {}).keys())
        except Exception:  # noqa: BLE001
            continue
    builtin = {"install", "ci", "test", "start", "exec", "dlx", "add", "create", "init", "dev"}
    for _, script in _CMD.findall(text):
        if script not in scripts and script not in builtin - {"dev"}:
            notes.append(f"README references script '{script}' that no package.json defines")
    arch_path = ctx.project / ".devpipeline" / "architecture.json"
    if arch_path.exists():
        for env in read_json(arch_path).get("env_vars", []):
            if env["name"] not in text and not any(env["name"] in p.read_text(errors="ignore") for p in ctx.wt.glob(".env.example")):
                notes.append(f"env var {env['name']} undocumented")
    if find_secrets(text):
        notes.append("README contains secret-looking values")
    return {"status": "FAIL" if notes else "PASS", "reason": "; ".join(notes) or "README complete", "evidence": [str(readme)]}


# --------------------------------------------------------------- issues ----
def _manifests(wt: Path) -> list[str]:
    names = ("package.json", "pyproject.toml", "requirements.txt", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "Cargo.toml")
    found = [n for n in names if (wt / n).exists()] + [p.name for p in wt.glob("*.csproj")]
    if not found:
        found = [f"{d.name}/{n}" for d in sorted(wt.iterdir()) if d.is_dir() and not d.name.startswith(".") for n in names if (d / n).exists()]
    return found[:5]


def collect_observations(ctx: Context, gate_results: dict) -> tuple[list[dict], set[str]]:
    """Return (observed issues, set of source refs that were freshly evaluated)."""
    obs: list[dict] = []
    fresh: set[str] = set()
    baseline_fail = {c["name"] for c in (ctx.baseline or {}).get("checks", []) if c["status"] in ("FAIL", "ERROR")}
    rep = ctx.latest_checks
    gating_checks = {name for r in gate_results.values() if r.get("mandatory") and r["status"] not in ("PASS", "NOT_APPLICABLE")
                     for name in r.get("checks", [])}
    fresh.add("ownership")
    for v in ctx.m.get("ownership_violations", []):
        if v["scope"] == "user-checkout" and not v.get("resolved"):
            obs.append({"source_ref": "ownership", "source_agent": "pipeline-operator", "category": "integration", "severity": "critical",
                        "check_name": "ownership:user-checkout", "title": "User checkout changed during the run",
                        "description": "The user's checkout changed while DevPipeline worked in worktrees (" + ", ".join(v["files"][:10])
                                       + "). Human review required; DevPipeline does not modify the user's checkout.",
                        "affected_files": [], "blocking": True, "assigned_agent": "integration-engineer"})
            continue
        if not v.get("resolved"):
            obs.append({"source_ref": "ownership", "source_agent": "pipeline-operator", "category": "code_quality", "severity": "high",
                        "check_name": f"ownership:{v['scope']}", "title": f"{v['scope']} step modified files outside its allowed paths",
                        "description": f"Files changed outside allowed paths during {v['scope']}: {', '.join(v['files'][:20])}",
                        "affected_files": v["files"][:50], "blocking": True, "assigned_agent": "integration-engineer"})
    if rep and rep.get("source_fingerprint") == ctx.code_fp:
        fresh.add("checks")
        for c in rep["checks"]:
            if c["status"] != "PASS" and c["name"] in gating_checks:
                pre = c["name"] in baseline_fail
                obs.append({"source_ref": "checks", "source_agent": "pipeline-operator", "category": CHECK_KIND_TO_CATEGORY.get(c["kind"], "build"),
                            "severity": "high", "check_name": c["name"], "title": f"Check {c['name']} {c['status']}",
                            "description": f"{c['name']} ({c.get('command') or 'no command'}) finished {c['status']}" + (f": {c['reason']}" if c.get("reason") else ""),
                            "affected_files": [c["cwd"]] if c["cwd"] != "." else [], "evidence": [c["log_path"]] if c.get("log_path") else [],
                            "blocking": True, "pre_existing": pre, "status": "PRE_EXISTING" if pre else "OPEN"})
    for kind, (f, r) in ctx.kind_reports.items():
        if r.get("_invalid") or r.get("source_fingerprint") != ctx.prod_fp:
            continue
        fresh.add(f"report:{kind}")
        for i in r.get("issues", []):
            obs.append({**i, "source_ref": f"report:{kind}"})
    for kind, (f, rv) in ctx.reviews.items():
        if rv.get("_invalid") or rv.get("source_fingerprint") != ctx.code_fp:
            continue
        fresh.add(f"review:{kind}")
        for i in rv.get("blocking_issues", []):
            obs.append({**i, "blocking": True, "source_ref": f"review:{kind}"})
    covered = {o.get("check_name") for o in obs}
    for gid, res in gate_results.items():
        if not res.get("mandatory") or res["status"] in ("PASS", "NOT_APPLICABLE") or gid in ("documentation", "final_review",
                                                                                                "requirements_coverage", "task_completion"):
            continue
        if res.get("checks") and any(c in covered for c in res["checks"]):
            continue
        if "no " in res.get("reason", "") and "command discovered" in res.get("reason", ""):
            fresh.add(f"gate:{gid}")
            obs.append({"source_ref": f"gate:{gid}", "source_agent": "pipeline-operator", "category": "test", "severity": "high",
                        "check_name": f"gate:{gid}", "title": f"Mandatory gate {gid} has no executable check",
                        "description": f"{gid}: {res['reason']}. Add the missing project script/test suite (e.g. package.json script).",
                        "affected_files": _manifests(ctx.wt), "blocking": True})
    for gid in ("documentation",):
        res = gate_results.get(gid)
        fresh.add(f"gate:{gid}")
        if res and res["status"] == "FAIL" and res.get("mandatory"):
            obs.append({"source_ref": f"gate:{gid}", "source_agent": "pipeline-operator", "category": "documentation", "severity": "medium",
                        "check_name": gid, "title": "Documentation gate failed", "description": res["reason"], "blocking": True,
                        "assigned_agent": "technical-writer"})
    return obs, fresh


def _conform_issue(issue: dict) -> dict:
    """Coerce agent-supplied issues onto issue.schema.json (drop unknown keys, fix enums)."""
    from dpcore.jsonschema_lite import load_schema

    schema = load_schema("issue.schema.json")
    props = schema["properties"]
    out = {k: v for k, v in issue.items() if k in props}
    out["description"] = out.get("description") or out.get("title") or "(no description provided)"
    if out.get("severity") not in props["severity"]["enum"]:
        out["severity"] = "medium"
    if out.get("category") not in props["category"]["enum"]:
        out["category"] = "functional"
    if out.get("source_agent") and not re.match(r"^[a-z][a-z0-9-]*$", out["source_agent"]):
        out["source_agent"] = re.sub(r"[^a-z0-9-]", "-", out["source_agent"].lower()).strip("-") or "pipeline-operator"
    for key in ("requirement_ids", "acceptance_criteria"):
        pattern = props[key]["items"]["pattern"]
        out[key] = [x for x in out.get(key, []) if isinstance(x, str) and re.match(pattern, x)]
    errs = validate_named(out, "issue.schema.json")
    if errs:
        raise DPError(f"cannot normalise issue {out.get('id')}: {errs[0]}")
    return out


def sync_issues(ctx: Context, observations: list[dict], fresh: set[str]) -> list[dict]:
    idir = ctx.rd / "issues"
    index_path = idir / "index.json"
    index = read_json(index_path, default={"next": 1, "by_fingerprint": {}}) if index_path.exists() else {"next": 1, "by_fingerprint": {}}
    iteration = ctx.m["counters"]["repair_iterations"]
    normalized = issuemod.dedupe([issuemod.normalize({k: v for k, v in o.items() if k != "source_ref"}, o.get("source_agent"))
                                  | {"source_ref": o.get("source_ref", "")} for o in observations])
    seen = set()
    out = []
    for issue in normalized:
        fp = issue["fingerprint"]
        iid = index["by_fingerprint"].get(fp)
        if not iid:
            iid = f"ISSUE-{index['next']:03d}"
            index["next"] += 1
            index["by_fingerprint"][fp] = iid
        path = idir / f"{iid}.json"
        prev = read_json(path) if path.exists() else {}
        ref = issue.pop("source_ref", "")
        issue["id"] = iid
        issue["assigned_agent"] = issuemod.route(issue, ctx.tasks, ctx.selected)
        issue["attempts"] = prev.get("attempts", 0)
        issue["first_seen_iteration"] = prev.get("first_seen_iteration", iteration)
        issue["last_seen_iteration"] = iteration
        if issue.get("pre_existing"):
            issue["status"] = "PRE_EXISTING"
        elif issue.get("status") not in ("OPEN", "PRE_EXISTING"):
            issue["status"] = "OPEN"
        issue = _conform_issue(issue)
        atomic_write_json(path, issue)
        seen.add(iid)
        out.append(issue | {"_source_ref": ref})
        (idir / f"{iid}.source").write_text(ref)
    for path in sorted(idir.glob("ISSUE-*.json")):
        if path.stem in seen:
            continue
        issue = read_json(path)
        src_file = idir / f"{path.stem}.source"
        ref = src_file.read_text() if src_file.exists() else ""
        if issue["status"] in ("OPEN", "IN_REPAIR") and ref in fresh:
            issue["status"] = "RESOLVED"
            atomic_write_json(path, issue)
    atomic_write_json(index_path, index)
    return out


# ----------------------------------------------------------------- main ----
def evaluate(project: Path, run_id: str) -> dict:
    ctx = Context(project, run_id)
    gdefs = cfgmod.gates_config()["gates"]
    results: dict[str, dict] = {}
    deferred = []
    for g in gdefs:
        ok, why = applies(g, ctx)
        level, lwhy = level_for(g, ctx)
        if not ok:
            results[g["id"]] = {"id": g["id"], "status": "NOT_APPLICABLE", "mandatory": False, "level": level, "reason": why}
            continue
        if level == "disabled":
            results[g["id"]] = {"id": g["id"], "status": "NOT_RUN", "mandatory": False, "level": level, "reason": "disabled by profile (" + why + ")"}
            continue
        src = g["source"]
        if src in ("issues", "report"):
            deferred.append((g, level, lwhy, why))
            continue
        if src == "checks":
            res = eval_checks(g, ctx)
        elif src == "review":
            res = eval_review(g, ctx)
        elif src == "plan_validation":
            pv = ctx.m.get("plan_validation")
            res = {"status": "PASS" if pv and pv.get("valid") else ("NOT_RUN" if ctx.mode == "audit" and not pv else "FAIL"),
                   "reason": "plan validated" if pv and pv.get("valid") else "plan not validated"}
            if ctx.mode == "audit" and not pv:
                res = {"status": "NOT_APPLICABLE", "reason": "audit mode has no task plan"}
        elif src == "tasks":
            bad = [t["id"] + ":" + t["status"] for t in ctx.tasks if t["status"] != "COMPLETED"
                   and not (t["status"] == "CANCELLED" and str(t.get("failure_reason", "")).startswith("superseded"))]
            res = {"status": "FAIL" if bad or not ctx.tasks else "PASS",
                   "reason": ("incomplete tasks: " + ", ".join(bad)) if bad else ("no tasks" if not ctx.tasks else "all tasks completed")}
        elif src == "coverage":
            res = eval_coverage(ctx)
        elif src == "documentation":
            res = eval_documentation(ctx)
        else:
            res = {"status": "ERROR", "reason": f"unknown gate source {src}"}
        results[g["id"]] = {"id": g["id"], "mandatory": level == "mandatory", "level": level, "applies_because": why, "level_because": lwhy, **res}

    observations, fresh = collect_observations(ctx, results)
    issues = sync_issues(ctx, observations, fresh)
    open_issues = [i for i in issues if i["status"] == "OPEN"]
    for g, level, lwhy, why in deferred:
        if g["source"] == "report":
            res = eval_report(g, ctx, open_issues)
        else:
            blocking = [i for i in open_issues if i["blocking"]]
            res = {"status": "FAIL" if blocking else "PASS",
                   "reason": (f"{len(blocking)} open blocking issue(s): " + ", ".join(i["id"] for i in blocking)) if blocking else "no open blocking issues"}
        results[g["id"]] = {"id": g["id"], "mandatory": level == "mandatory", "level": level, "applies_because": why, "level_because": lwhy, **res}

    ordered = [results[g["id"]] for g in gdefs]
    mandatory_bad = [r for r in ordered if r["mandatory"] and r["status"] not in ("PASS", "NOT_APPLICABLE")]
    pre_existing = [i for i in issues if i["status"] == "PRE_EXISTING"]
    regressions = [i for i in open_issues if i["blocking"]]
    passed = not mandatory_bad
    pre_only = bool(mandatory_bad) and not regressions and bool(pre_existing)
    with sm.mutate(ctx.project, run_id) as m:
        seq = len(m["gate_results"]) + 1
        name = f"gates/gate-{seq:03d}.json"
        m["gate_results"].append({"seq": seq, "path": name, "passed": passed, "at": now_iso(), "fingerprint": ctx.code_fp})
        m["open_issues"] = [i["id"] for i in open_issues]
        hist = issuemod.update_history(m.setdefault("issue_history", {}), [i["fingerprint"] for i in open_issues if i["blocking"]],
                                       m["counters"]["repair_iterations"])
        m["issue_history"] = hist
    gate = {"run_id": run_id, "seq": seq, "created_at": now_iso(), "code_fingerprint": ctx.code_fp, "prod_fingerprint": ctx.prod_fp,
            "docs_fingerprint": ctx.docs_fp,
            "passed": passed, "overall": "PASS" if passed else "FAIL", "gates": ordered,
            "failing_mandatory": [r["id"] for r in mandatory_bad],
            "blocking_issue_ids": [i["id"] for i in regressions], "pre_existing_issue_ids": [i["id"] for i in pre_existing],
            "pre_existing_only": pre_only, "open_issue_count": len(open_issues)}
    atomic_write_json(ctx.rd / name, gate)
    sm.event(ctx.project, run_id, "QUALITY_GATE_PASSED" if passed else "QUALITY_GATE_FAILED", phase=ctx.m["phase"],
             status=gate["overall"], artifacts=[name], detail=",".join(gate["failing_mandatory"]))
    for i in regressions:
        sm.event(ctx.project, run_id, "ISSUE_DETECTED", agent=i["assigned_agent"], status=i["severity"], detail=i["id"])
    return {"ok": True, "passed": passed, "overall": gate["overall"], "gate_file": name,
            "failing_mandatory": gate["failing_mandatory"], "blocking_issue_ids": gate["blocking_issue_ids"],
            "blocking_categories": sorted({i["category"] for i in regressions}),
            "pre_existing_only": pre_only,
            "gates": {r["id"]: r["status"] for r in ordered}}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--run", required=True)
    a = ap.parse_args(argv)
    try:
        return emit(evaluate(Path(a.project), a.run))
    except DPError as exc:
        return emit({"ok": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
