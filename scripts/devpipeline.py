#!/usr/bin/env python3
"""DevPipeline operations CLI — the single deterministic entry point used by
command skills (main session) and by workflow scripts (via pipeline-operator).

Every command prints exactly one JSON object (with an integrity `checksum`)
on stdout. Exit code 0 = ok, 1 = handled error ({"ok": false, "error": ...}).

Main-session commands:
  prepare      --project DIR --mode build|improve|audit|repair [--request-file F | --request TEXT]
               [--profile P] [--overrides-json JSON] [--new]
  status       --project DIR [--run ID]
  resume       --project DIR [--run ID] [--approve-note TEXT]
  version

Workflow operations (always --project DIR --run ID):
  context | transition --to PHASE [--reason R] [--step S] [--counters-b64 B]
  validate-requirements | validate-plan | baseline
  start-tasks --tasks T1,T2 | finish-tasks --tasks T1,T2 [--returned-b64 B]
  verify-merge --tasks T1 | integration-status | run-checks --label L [--only kinds]
  commit-integration --scope qa|docs|integration --message M
  validate-reports --expect kind:agent,... | gate | plan-repair --iteration N | finalize --status S
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dpcore import ROOT, framework_version  # noqa: E402
from dpcore import config as cfgmod  # noqa: E402
from dpcore import gitutil  # noqa: E402
from dpcore import issues as issuemod  # noqa: E402
from dpcore.dag import blocked_by_failure, path_owned  # noqa: E402
from dpcore.jsonschema_lite import validate_named  # noqa: E402
from dpcore.selection import detect_features, merge_features, select_agents  # noqa: E402
from dpcore.util import (DPError, atomic_write_json, atomic_write_text, decode_b64_json, emit, emit_line, file_lock,  # noqa: E402
                         now_iso, read_json, relay_text, write_stdout)
import quality_gate  # noqa: E402
import report_generator  # noqa: E402
import run_checks  # noqa: E402
import state_manager as sm  # noqa: E402
import validate_contracts as vc  # noqa: E402
import worktree_manager as wm  # noqa: E402

QA_KINDS = cfgmod.qa_kinds()
# Bump when the JSON contract between workflows/*.js and these helpers changes. The plugin is read
# from its folder, so helpers can be newer than a workflow that is already running.
PROTOCOL = 2  # single source: config/agent-registry.json report_kind
DOC_SCOPE = ["*.md", "**/*.md", "docs/", ".env.example", "LICENSE"]
PLANNING_FILES = ["requirements.json", "PRD.md", "architecture.json", "ARCHITECTURE.md", "tasks.json", "adr/", "contracts/",
                  "acceptance-matrix.md", "contract-changes.json"]


# ------------------------------------------------------------- helpers ----
def P(project: str) -> Path:
    return Path(project).expanduser().resolve()


def tasks_file(project: Path, run_id: str) -> Path:
    return sm.run_dir(project, run_id) / "tasks.json"


def load_tasks(project: Path, run_id: str) -> list[dict]:
    f = tasks_file(project, run_id)
    return read_json(f)["tasks"] if f.exists() else []


def save_tasks(project: Path, run_id: str, tasks: list[dict]) -> None:
    doc = {"run_id": run_id, "tasks": tasks}
    errs = validate_named(doc, "task.schema.json")
    if errs:
        raise DPError("refusing to persist invalid tasks: " + "; ".join(errs[:3]))
    atomic_write_json(tasks_file(project, run_id), doc)


def compact(t: dict) -> dict:
    """What the workflow needs for scheduling — no free text (titles stay in tasks.json)."""
    return {"id": t["id"], "agent": t["agent"], "kind": t.get("kind", "implementation"),
            "deps": t.get("dependencies", []), "status": t["status"], "attempt": t["attempt"]}


def active_path(project: Path) -> Path:
    return sm.dp_dir(project) / "active.json"


def registry_path() -> Path:
    """User-level index of active runs so hooks can find a run when the agent's cwd is elsewhere."""
    import os
    return Path(os.environ.get("DEVPIPELINE_STATE_DIR", Path.home() / ".local" / "state" / "devpipeline")) / "active-runs.json"


def update_registry(project: Path, run_id: str, active: bool, phase: str = "") -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path.with_suffix(".lock")):
        reg = read_json(path, default={"runs": {}}) if path.exists() else {"runs": {}}
        key = str(Path(project).resolve())
        if active:
            reg["runs"][key] = {"run_id": run_id, "phase": phase, "updated_at": now_iso()}
        else:
            reg["runs"].pop(key, None)
        atomic_write_json(path, reg)


def write_active(project: Path, run_id: str, **updates) -> dict:
    path = active_path(project)
    with file_lock(sm.dp_dir(project) / "active.lock"):
        cur = read_json(path) if path.exists() else {}
        if cur.get("run_id") != run_id:
            cur = {"run_id": run_id, "active_tasks": {}}
        for k, v in updates.items():
            if k == "active_tasks_add":
                cur["active_tasks"].update(v)
            elif k == "active_tasks_remove":
                for tid in v:
                    cur["active_tasks"].pop(tid, None)
            else:
                cur[k] = v
        cur["updated_at"] = now_iso()
        atomic_write_json(path, cur)
    update_registry(project, run_id, True, cur.get("phase", ""))
    return cur


def sync_task_map(project: Path, run_id: str, tasks: list[dict], **manifest_updates) -> None:
    with sm.mutate(project, run_id) as m:
        m["tasks"] = {t["id"]: {"status": t["status"], "attempt": t["attempt"], "agent": t["agent"]} for t in tasks}
        m.update(manifest_updates)


# -------------------------------------------------------- main session ----
def _invocation() -> str:
    import secrets
    return secrets.token_hex(4)  # distinguishes workflow invocations for the op cache


def op_relay_failure(project: Path, run_id: str, op_key: str, relayed: dict) -> dict:
    """Store what the relay delivered next to what the helper really printed (diagnostics)."""
    ops = sm.run_dir(project, run_id) / "ops"
    true_line = (ops / f"{op_key}.json").read_text() if (ops / f"{op_key}.json").exists() else ""
    truth = json.loads(true_line) if true_line else {}
    diffs = []

    def walk(a, b, path="$"):
        if len(diffs) >= 20:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                if k not in a or k not in b:
                    diffs.append(f"{path}.{k}: {'missing in relay' if k not in b else 'extra in relay'}")
                else:
                    walk(a[k], b[k], f"{path}.{k}")
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                diffs.append(f"{path}: list length {len(a)} vs relayed {len(b)}")
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{path}[{i}]")
        elif a != b:
            diffs.append(f"{path}: {json.dumps(a, ensure_ascii=False)[:80]} != relayed {json.dumps(b, ensure_ascii=False)[:80]}")

    walk(truth, relayed)
    path = ops / f"relay-failure-{op_key}.json"
    atomic_write_json(path, {"op_key": op_key, "at": now_iso(), "differences": diffs, "true_output": truth, "relayed": relayed})
    sm.event(project, run_id, "RELAY_INTEGRITY_FAILED", detail=f"{op_key}: {'; '.join(diffs[:3])}")
    return {"ok": True, "diagnostics": str(path), "differences": len(diffs)}


def cmd_prepare(a) -> dict:
    project = P(a.project)
    has_content = project.exists() and any(p for p in project.iterdir() if p.name not in (".devpipeline", ".git", ".DS_Store", ".gitignore"))
    if a.reuse_plan:
        for f in ("requirements.json", "architecture.json", "tasks.json"):
            if not (project / ".devpipeline" / f).exists():
                raise DPError(f"--reuse-plan: .devpipeline/{f} is missing; run a normal build instead")
        given = (a.request or "").strip() or (Path(a.request_file).read_text(encoding="utf-8").strip() if a.request_file and a.request_file != "/dev/stdin" and Path(a.request_file).exists() else "")
        if a.request_file == "/dev/stdin":
            given = sys.stdin.read().strip()
            a.request, a.request_file = given, None
        if not given:
            prev = sorted((project / ".devpipeline" / "runs").glob("*/request.txt"), key=lambda p: p.stat().st_mtime)
            if prev:
                a.request, a.request_file = None, str(prev[-1])
    if a.new and has_content:
        raise DPError(f"{project} already contains files; refusing to scaffold a new project over it — use improve mode")
    new = not has_content
    if a.mode in ("audit", "repair", "improve") and (not project.exists() or new):
        raise DPError(f"{a.mode} mode requires an existing project directory with code")
    project.mkdir(parents=True, exist_ok=True)
    request = Path(a.request_file).read_text(encoding="utf-8") if a.request_file else (a.request or "")
    if not request.strip() and a.mode in ("build", "improve"):
        raise DPError("a natural-language request is required for build/improve")
    overrides = json.loads(a.overrides_json) if a.overrides_json else {}
    issue_ids = [x for x in (a.issues or "").split(",") if x]
    if a.mode == "repair" and not (a.source_run and issue_ids):
        raise DPError("repair mode requires --source-run RUN and --issues ISSUE-001,... (user-approved findings)")
    sm.ensure_project(project, created_by_devpipeline=new, profile=a.profile)
    m = sm.create_run(project, a.mode, request, a.profile, overrides, "new" if new else "existing")
    script = ROOT / "workflows" / f"{a.mode}-project.js"
    return {"ok": True, "run_id": m["run_id"], "project_dir": str(project), "project_mode": m["project_mode"],
            "profile": m["config"]["profile"], "workflow_script": str(script),
            "workflow_args": {"plugin_root": str(ROOT), "project_dir": str(project), "run_id": m["run_id"],
                              "agent_prefix": "devpipeline:", "started_at": m["started_at"], "invocation": _invocation(),
                              **({"reuse_plan": True} if a.reuse_plan else {}),
                              **({"source_run": a.source_run, "issue_ids": issue_ids} if a.mode == "repair" else {})}}


def cmd_resume(a) -> dict:
    project = P(a.project)
    chk = sm.resume_check(project, a.run)
    if not chk["ok"]:
        return {"ok": False, "error": "; ".join(chk["problems"]), **chk}
    m = sm.load_manifest(project, chk["run_id"])
    if a.approve_note is not None and m["phase"] == "AWAITING_APPROVAL":
        sm.approve(project, chk["run_id"], a.approve_note or "approved by user")
    if a.retry_failed:
        tasks = load_tasks(project, chk["run_id"])
        retried = []
        for t in tasks:
            if t["status"] in ("FAILED", "BLOCKED") and not str(t.get("failure_reason", "")).startswith("superseded"):
                t["status"], t["attempt"] = "PENDING", 0
                retried.append(t["id"])
        if retried:
            save_tasks(project, chk["run_id"], tasks)
            sync_task_map(project, chk["run_id"], tasks)
            sm.event(project, chk["run_id"], "TASKS_RETRIED", detail=f"user-approved retry: {retried}")
        chk["retried_tasks"] = retried
    if m["phase"] not in cfgmod.state_machine()["resumable"]:
        # an interrupted run (session ended mid-phase) restarts from a resumable state
        sm.transition(project, chk["run_id"], "BLOCKED", f"interrupted during {m['phase']}; resuming")
    script = ROOT / "workflows" / f"{m['mode']}-project.js"
    return {"ok": True, **chk, "workflow_script": str(script),
            "workflow_args": {"plugin_root": str(ROOT), "project_dir": str(project), "run_id": chk["run_id"],
                              "agent_prefix": "devpipeline:", "resume": True, "invocation": _invocation(),
                              **({"source_run": m["repair_scope"]["source_run"], "issue_ids": m["repair_scope"]["issue_ids"]}
                                 if m.get("repair_scope") else {})}}


# ---------------------------------------------------------- workflow ops ----
def op_context(project: Path, run_id: str) -> dict:
    m = sm.load_manifest(project, run_id)
    src = m.get("source", {})
    if m["mode"] != "audit" and not src.get("integration_worktree"):
        info = wm.setup_integration(project, run_id, m["project_mode"])
        with sm.mutate(project, run_id) as mm:
            mm["source"].update(info)
            mm["isolation"] = info["isolation"]
            if info["isolation"] == "worktree":
                mm["source"]["user_checkout"] = wm.checkout_state(project)
        src = info
        sm.event(project, run_id, "PREFLIGHT_COMPLETED", phase=m["phase"], detail=f"isolation={info['isolation']}")
    elif m["mode"] == "audit" and not src.get("integration_worktree"):
        info = wm.setup_audit(project, run_id)
        with sm.mutate(project, run_id) as mm:
            mm["source"].update(info)
            mm["isolation"] = "read-only"
            mm["features"] = detect_features(project)
        src = sm.load_manifest(project, run_id)["source"]
    m = sm.load_manifest(project, run_id)
    pcfg = read_json(sm.dp_dir(project) / "config.json")
    write_active(project, run_id, mode=m["mode"], project_dir=str(project),
                 integration_worktree=src.get("integration_worktree"), qa_write_paths=pcfg.get("qa_write_paths", []),
                 protected_paths=pcfg.get("protected_paths", []), phase=m["phase"])
    cfg = m["config"]
    keep = ["profile", "max_parallel_agents", "max_agent_calls", "max_operator_calls", "max_repair_iterations",
            "max_task_retries", "max_no_progress_iterations", "max_output_tokens", "require_plan_approval",
            "auto_accept_assumptions", "live_ai_evaluation", "viewports", "model_routing", "effort_routing"]
    tasks = load_tasks(project, run_id)
    rd = sm.run_dir(project, run_id)
    return {"ok": True, "run_id": run_id, "mode": m["mode"], "project_mode": m["project_mode"], "phase": m["phase"],
            "completed_steps": m["completed_steps"], "counters": m["counters"], "config": {k: cfg.get(k) for k in keep},
            "isolation": m.get("isolation", src.get("isolation")), "features": m.get("features", {}),
            "selected_agents": [x["agent"] for x in m.get("selected_agents", [])],
            "approvals": len(m.get("approvals", [])),
            "questions_approved": any(x.get("kind") == "questions" for x in m.get("approvals", [])),
            "plan_approved": any(x.get("kind") == "plan" for x in m.get("approvals", [])),
            "approval_notes": [x.get("note", "") for x in m.get("approvals", []) if x.get("kind") == "questions"],
            "qa_kinds": cfgmod.qa_kinds(),
            "paths": {"project": str(project), "dp": str(sm.dp_dir(project)), "run_dir": str(rd),
                      "integration": src.get("integration_worktree"), "request": str(rd / "request.txt"),
                      "contracts": str(sm.dp_dir(project) / "contracts"), "templates": str(ROOT / "templates"),
                      "schemas": str(ROOT / "schemas")},
            "tasks": [compact(t) for t in tasks], "conflicts": m.get("conflicts", []),
            "source": {"base_revision": src.get("base_revision"), "integration_branch": src.get("integration_branch"),
                       "integration_head": src.get("integration_head")},
            "warnings": src.get("warnings", [])}


def op_transition(project: Path, run_id: str, a) -> dict:
    counters = decode_b64_json(a.counters_b64, {})
    res = sm.transition(project, run_id, a.to, a.reason or "", counters, a.step)
    if a.to == "AWAITING_APPROVAL" and a.awaiting:
        with sm.mutate(project, run_id) as mm:
            mm["awaiting"] = a.awaiting
    write_active(project, run_id, phase=res["phase"])
    return res


def op_validate_requirements(project: Path, run_id: str) -> dict:
    path = sm.dp_dir(project) / "requirements.json"
    if not path.exists():
        return {"ok": True, "valid": False, "errors": ["requirements.json was not written"], "blocking_questions": []}
    req = read_json(path)
    res = vc.check_requirements(req)
    if res["valid"]:
        with sm.mutate(project, run_id) as m:
            m["features"] = merge_features(m.get("features"), req.get("features"))
    return {"ok": True, **res, "errors": res["errors"][:15]}


def op_validate_plan(project: Path, run_id: str) -> dict:
    dp = sm.dp_dir(project)
    errors: list[str] = []
    req = read_json(dp / "requirements.json") if (dp / "requirements.json").exists() else None
    arch = read_json(dp / "architecture.json") if (dp / "architecture.json").exists() else None
    tdoc = read_json(dp / "tasks.json") if (dp / "tasks.json").exists() else None
    if arch is None:
        errors.append("architecture.json was not written")
    if tdoc is None:
        errors.append("tasks.json was not written")
    if errors:
        return {"ok": True, "valid": False, "errors": errors}
    ares = vc.check_architecture(arch, req)
    tres = vc.check_tasks(tdoc, req)
    errors = ares["errors"] + tres["errors"]
    m = sm.load_manifest(project, run_id)
    detected = detect_features(Path(m["source"]["integration_worktree"])) if m.get("project_mode") == "existing" and m["source"].get("integration_worktree") else {}
    if any(t.get("agent") == "ai-engineer" for t in tdoc.get("tasks", [])):
        detected = {**detected, "ai": True}
    features = merge_features(m.get("features"), (req or {}).get("features"), arch.get("features"), detected)
    sel = select_agents(features, m["config"]["profile"], m["mode"], arch.get("required_agents"),
                        sorted({t["agent"] for t in tdoc.get("tasks", [])}), m["config"].get("agents"))
    errors += [e for e in sel["errors"] if not e.startswith("cannot disable")]
    warnings = ares["warnings"] + tres["warnings"] + [e for e in sel["errors"] if e.startswith("cannot disable")]
    valid = not errors
    if valid:
        existing = {t["id"]: t for t in load_tasks(project, run_id)}
        tasks = []
        for t in tdoc["tasks"]:
            cur = existing.get(t["id"])
            tasks.append(cur if cur else {**t, "status": "PENDING", "attempt": 0})
        save_tasks(project, run_id, tasks)
        sync_task_map(project, run_id, tasks, features=features, selected_agents=sel["selected"],
                      agent_decisions=sel["decisions"], conflicts=tres["conflicts"],
                      plan_validation={"valid": True, "at": now_iso(), "warnings": warnings})
        for s in sel["selected"]:
            sm.event(project, run_id, "AGENT_SELECTED", agent=s["agent"], detail=s["reason"])
    else:
        with sm.mutate(project, run_id) as mm:
            mm["plan_validation"] = {"valid": False, "at": now_iso(), "errors": errors[:30]}
    selected = [s["agent"] for s in sel["selected"]]
    return {"ok": True, "valid": valid, "errors": errors[:15], "warnings": warnings[:15],
            "tasks": [compact(t) for t in load_tasks(project, run_id)] if valid else [],
            "conflicts": tres["conflicts"], "selected_agents": selected,
            "qa_agents": [a for a in selected if a in QA_KINDS], "features": features}


def op_baseline(project: Path, run_id: str) -> dict:
    res = run_checks.run(project, run_id, "baseline", baseline=True)
    sm.event(project, run_id, "TEST_COMPLETED", detail=f"baseline {res['status']}", artifacts=[res["report"]])
    return {"ok": True, "status": res["status"], "report": res["report"],
            "failing": [c["name"] for c in res["checks"] if c["status"] != "PASS"]}


def op_start_tasks(project: Path, run_id: str, ids: list[str]) -> dict:
    m = sm.load_manifest(project, run_id)
    tasks = load_tasks(project, run_id)
    by_id = {t["id"]: t for t in tasks}
    integ = m["source"]["integration_worktree"]
    iso = m.get("isolation", "worktree")
    if iso != "worktree" and len(ids) > 1:
        raise DPError("serial isolation: only one writing task may run at a time")
    started, active = [], {}
    for tid in ids:
        t = by_id.get(tid)
        if not t:
            raise DPError(f"unknown task {tid}")
        if t["status"] != "PENDING":
            raise DPError(f"{tid} is {t['status']}, not PENDING")
        unmet = [d for d in t.get("dependencies", []) if by_id.get(d, {}).get("status") != "COMPLETED"]
        if unmet:
            raise DPError(f"{tid} has unmet dependencies {unmet}")
        info = wm.create_task(project, run_id, tid, integ, iso)
        t.update({"status": "RUNNING", "attempt": t["attempt"] + 1, "worktree": info["worktree"]})
        if info.get("branch"):
            t["branch"], t["base_revision"] = info["branch"], info["base_revision"]
        if info.get("snapshot"):
            t["_snapshot"] = info["snapshot"]
        if info.get("previous_attempt_branch"):
            t["failure_reason"] = (t.get("failure_reason", "") + f" | previous attempt preserved on branch {info['previous_attempt_branch']}")[:1000]
        result_path = sm.run_dir(project, run_id) / "agent-results" / f"{tid}.json"
        if result_path.exists():
            result_path.rename(result_path.with_suffix(f".attempt{t['attempt'] - 1}.json"))
        active[tid] = {"agent": t["agent"], "worktree": info["worktree"], "owned_paths": t.get("owned_paths", []) + t.get("shared_paths", [])}
        started.append({"task_id": tid, "agent": t["agent"], "title": relay_text(t["title"], 120), "worktree": info["worktree"],
                        "result_path": str(result_path), "owned_paths": t.get("owned_paths", []), "attempt": t["attempt"],
                        "kind": t.get("kind", "implementation"), "issue_ids": t.get("issue_ids", []),
                        "failure_reason": relay_text(t.get("failure_reason", ""), 300),
                        "previous_attempt_branch": info.get("previous_attempt_branch")})
        sm.event(project, run_id, "TASK_STARTED", agent=t["agent"], task_id=tid, detail=f"attempt {t['attempt']}")
    save_tasks(project, run_id, [{k: v for k, v in t.items() if not k.startswith("_")} for t in tasks])
    # snapshots for serial mode are kept in active.json (not part of task schema)
    for tid in ids:
        if by_id[tid].get("_snapshot"):
            active[tid]["snapshot"] = by_id[tid]["_snapshot"]
    write_active(project, run_id, active_tasks_add=active)
    sync_task_map(project, run_id, load_tasks(project, run_id))
    return {"ok": True, "started": started}


def verify_user_checkout(project: Path, run_id: str, scope: str) -> list[str]:
    """Record a blocking violation when the user's checkout changed during a worktree-isolated run."""
    m = sm.load_manifest(project, run_id)
    before = m.get("source", {}).get("user_checkout")
    if not before or m.get("isolation") != "worktree":
        return []
    now = wm.checkout_state(project)
    changed = sorted({line[3:] if len(line) > 3 and line[2] == " " else line
                      for line in set(now.get("status", [])) ^ set(before.get("status", []))})
    changed += sorted(p for p, h in now.get("hashes", {}).items() if before.get("hashes", {}).get(p) != h and p not in changed)
    if now.get("head") != before.get("head"):
        changed.append(f"HEAD moved {str(before.get('head'))[:10]} -> {str(now.get('head'))[:10]}")
    if changed:
        with sm.mutate(project, run_id) as mm:
            known = {tuple(v["files"]) for v in mm.get("ownership_violations", []) if v["scope"] == "user-checkout"}
            if tuple(changed[:50]) not in known:
                mm.setdefault("ownership_violations", []).append({"scope": "user-checkout", "files": changed[:50], "at": now_iso(), "resolved": False,
                                                                  "note": f"detected at {scope}; changed by an agent or by the user during the run"})
        sm.event(project, run_id, "ISSUE_DETECTED", detail=f"user checkout changed during run: {changed[:5]}")
    return changed


def normalize_result(path: Path, run_id: str, task_id: str, agent: str) -> dict | None:
    """Repair harmless shape mistakes so a correct piece of work is not thrown away.

    Fills identity fields the coordinator knows (run_id, task_id, agent), converts string artifacts
    to objects, moves unknown validation keys into `summary`/`evidence_path`, and defaults empty
    lists. Status, summary and validation results are never invented; the file is rewritten so the
    normalised version is what the gate later reads.
    """
    try:
        res = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(res, dict):
        return None
    if res.get("task_id") not in (None, task_id) or res.get("agent") not in (None, agent) or res.get("run_id") not in (None, run_id):
        return res  # identity conflicts are real errors — leave them for validation
    res.update({"run_id": run_id, "task_id": task_id, "agent": agent})
    for key in ("changed_files", "validation", "issues", "artifacts", "requirements"):
        if res.get(key) is None:
            res[key] = []
    res["artifacts"] = [{"path": a} if isinstance(a, str) else a for a in res["artifacts"]]
    allowed = {"name", "status", "exit_code", "command", "cwd", "evidence_path", "summary"}
    fixed = []
    for v in res["validation"]:
        if not isinstance(v, dict):
            continue
        extra = {k: v.pop(k) for k in list(v) if k not in allowed}
        if extra:
            ev = extra.get("evidence") or extra.get("log") or extra.get("log_path")
            if isinstance(ev, str) and "evidence_path" not in v:
                v["evidence_path"] = ev
            rest = {k: x for k, x in extra.items() if k not in ("evidence", "log", "log_path")}
            if rest:
                v["summary"] = (str(v.get("summary", "")) + " " + json.dumps(rest, ensure_ascii=False))[:500].strip()
        for k in ("evidence_path", "command", "cwd", "summary"):
            if v.get(k) is None:
                v.pop(k, None)
        fixed.append(v)
    res["validation"] = fixed
    issues = []
    for n, i in enumerate(res["issues"], start=1):
        if isinstance(i, str):  # free-text notes become non-blocking, schema-valid issues
            i = {"id": f"ISSUE-{900 + n:03d}", "source_agent": agent, "assigned_agent": agent, "severity": "low",
                 "category": "functional", "description": i[:2000], "blocking": False, "status": "OPEN"}
        if isinstance(i, dict):
            issues.append(i)
    res["issues"] = issues
    atomic_write_json(path, res)
    return res


def op_finish_tasks(project: Path, run_id: str, ids: list[str], returned: dict) -> dict:
    m = sm.load_manifest(project, run_id)
    cfg = m["config"]
    iso = m.get("isolation", "worktree")
    integ = Path(m["source"]["integration_worktree"])
    tasks = load_tasks(project, run_id)
    by_id = {t["id"]: t for t in tasks}
    active = read_json(active_path(project)).get("active_tasks", {}) if active_path(project).exists() else {}
    out, conflicts = [], []
    for tid in ids:
        t = by_id[tid]
        rpath = sm.run_dir(project, run_id) / "agent-results" / f"{tid}.json"
        result = normalize_result(rpath, run_id, tid, t["agent"]) if rpath.exists() else None
        check = vc.check_result(result, tid, t["agent"], run_id)
        errors = list(check["errors"])
        if returned.get(tid) is None and result is None:
            errors.append("agent returned null (cancelled, API error or stall)")
        ti = {"task_id": tid, "worktree": t.get("worktree"), "base_revision": t.get("base_revision"),
              "snapshot": active.get(tid, {}).get("snapshot")}
        allowed = t.get("owned_paths", []) + t.get("shared_paths", [])
        fin = wm.finish_task(project, run_id, ti, iso, allowed if iso == "worktree" else None) if t.get("worktree") else {"changed_files": []}
        changed = fin.get("changed_files", [])
        reverted = fin.get("reverted", [])
        if reverted:  # the task keeps its legitimate work; the stray edits are undone and reported
            warnings_ = f"reverted changes outside owned paths: {reverted[:10]}"
            with sm.mutate(project, run_id) as mm:
                mm.setdefault("ownership_reverted", {}).setdefault(tid, [])
                mm["ownership_reverted"][tid] = sorted(set(mm["ownership_reverted"][tid]) | set(reverted))
            sm.event(project, run_id, "OWNERSHIP_REVERTED", agent=t["agent"], task_id=tid, detail=warnings_)
        outside = [f for f in changed if not path_owned(f, allowed)]
        if outside:  # serial mode (no worktree) cannot revert safely
            errors.append(f"ownership violation: changed files outside owned paths: {outside[:10]}")
        if result and set(result.get("changed_files", [])) - set(changed) - set(reverted) and iso == "worktree":
            claimed = sorted(set(result["changed_files"]) - set(changed) - set(reverted))
            errors.append(f"result claims changes that do not exist: {claimed[:10]}")
        status = "COMPLETED"
        if errors:
            status = "FAILED"
        elif result["status"] in ("BLOCKED", "NEEDS_INPUT"):
            status = "BLOCKED"
            errors.append("agent reported " + result["status"] + ": " + "; ".join(result.get("blockers", []))[:300])
        elif result["status"] != "COMPLETED":
            status = "FAILED"
            errors.append(f"agent reported {result['status']}")
        retry = False
        if status == "FAILED" and t["attempt"] <= cfg["max_task_retries"]:
            status, retry = "PENDING", True
        t["status"] = status
        t["result_revision"] = fin.get("result_revision") or t.get("result_revision")
        t["failure_reason"] = "; ".join(errors)[:1000] if errors else ""
        merged = None
        if status == "COMPLETED" and iso == "worktree":
            mr = wm.merge_task(integ, run_id, tid)
            merged = mr["merged"]
            if not merged:
                conflicts.append({"task_id": tid, "branch": wm.branch_for(run_id, tid), "files": mr.get("conflicts", []), "error": mr.get("error", "")})
        elif status == "COMPLETED":
            merged = True
        sm.event(project, run_id, "TASK_COMPLETED" if status == "COMPLETED" else "TASK_FAILED", agent=t["agent"], task_id=tid,
                 status=status, detail=t["failure_reason"][:300] or None)
        out.append({"id": tid, "status": status, "retry": retry, "merged": bool(merged), "errors": relay_text(errors[:3], 200), "changed": len(changed)})
    checkout_changes = verify_user_checkout(project, run_id, "finish-tasks")
    if checkout_changes:
        for r in out:
            r["errors"].append("user checkout changed during the task: " + ", ".join(checkout_changes[:5]))
    newly_blocked = blocked_by_failure(tasks)
    for tid in newly_blocked:
        by_id[tid]["status"] = "BLOCKED"
        by_id[tid]["failure_reason"] = "dependency failed"
    save_tasks(project, run_id, tasks)
    write_active(project, run_id, active_tasks_remove=ids)
    sync_task_map(project, run_id, tasks)
    if iso == "worktree":
        with sm.mutate(project, run_id) as mm:
            mm["source"]["integration_head"] = gitutil.head(integ)
    changed_ids = set(ids) | set(newly_blocked)
    return {"ok": True, "results": out, "conflicts": conflicts, "newly_blocked": newly_blocked,
            "updates": [{"id": t["id"], "status": t["status"], "attempt": t["attempt"]} for t in tasks if t["id"] in changed_ids]}


def op_verify_merge(project: Path, run_id: str, ids: list[str]) -> dict:
    m = sm.load_manifest(project, run_id)
    integ = Path(m["source"]["integration_worktree"])
    tasks = load_tasks(project, run_id)
    res = []
    before = {t["id"]: t["status"] for t in tasks}
    for t in tasks:
        if t["id"] not in ids:
            continue
        ok = gitutil.git(integ, "merge-base", "--is-ancestor", wm.branch_for(run_id, t["id"]), "HEAD", check=False).returncode == 0
        dirty = bool(gitutil.status_porcelain(integ))
        if not ok or dirty:
            t["status"] = "FAILED"
            t["failure_reason"] = "integration could not merge this task" + (" (integration worktree left dirty)" if dirty else "")
        res.append({"id": t["id"], "merged": ok and not dirty})
    for tid in blocked_by_failure(tasks):
        next(x for x in tasks if x["id"] == tid)["status"] = "BLOCKED"
    save_tasks(project, run_id, tasks)
    sync_task_map(project, run_id, tasks)
    with sm.mutate(project, run_id) as mm:
        mm["source"]["integration_head"] = gitutil.head(integ)
    return {"ok": True, "results": res,
            "updates": [{"id": t["id"], "status": t["status"], "attempt": t["attempt"]} for t in tasks if before.get(t["id"]) != t["status"]]}


def op_integration_status(project: Path, run_id: str) -> dict:
    m = sm.load_manifest(project, run_id)
    integ = Path(m["source"]["integration_worktree"])
    tasks = load_tasks(project, run_id)
    unmerged = []
    if m.get("isolation") == "worktree":
        for t in tasks:
            if t["status"] == "COMPLETED" and t.get("branch"):
                if gitutil.git(integ, "merge-base", "--is-ancestor", t["branch"], "HEAD", check=False).returncode != 0:
                    unmerged.append(t["id"])
    head = gitutil.head(integ) if gitutil.is_repo(integ) else None
    with sm.mutate(project, run_id) as mm:
        mm["source"]["integration_head"] = head
        mm["source"]["code_fingerprint"] = gitutil.tree_fingerprint(integ)
    sm.event(project, run_id, "INTEGRATION_STARTED", detail=f"head={head} unmerged={unmerged}")
    return {"ok": True, "integration_head": head, "unmerged": unmerged,
            "incomplete": [t["id"] for t in tasks if t["status"] != "COMPLETED"]}


def op_run_checks(project: Path, run_id: str, label: str, only: str | None) -> dict:
    res = run_checks.run(project, run_id, label, only=only.split(",") if only else None)
    sm.event(project, run_id, "BUILD_COMPLETED" if any(c["kind"] == "build" for c in res["checks"]) else "TEST_COMPLETED",
             status=res["status"], artifacts=[res["report"]])
    with sm.mutate(project, run_id) as mm:
        mm["source"]["code_fingerprint"] = res["fingerprint"]
    m = sm.load_manifest(project, run_id)
    added = []
    if m["mode"] in ("build", "improve", "repair"):  # AI code appearing during the run still gets AI evaluation
        feats = merge_features(m.get("features"), detect_features(Path(m["source"]["integration_worktree"])))
        sel = {a["agent"] for a in m.get("selected_agents", [])}
        if (feats.get("ai") or "ai-engineer" in sel) and "ai-evaluation-engineer" not in sel:
            added.append("ai-evaluation-engineer")
            with sm.mutate(project, run_id) as mm:
                mm["features"] = {**feats, "ai": True}
                mm["selected_agents"].append({"agent": "ai-evaluation-engineer", "reason": "rule: AI functionality detected in code", "mandatory": True, "source": "rule"})
            sm.event(project, run_id, "AGENT_SELECTED", agent="ai-evaluation-engineer", detail="AI functionality detected in code")
        m = sm.load_manifest(project, run_id)
    fps = sm.fingerprints(project, m)
    return {"ok": True, "status": res["status"], "report": res["report"], "fingerprint": res["fingerprint"],
            "selected_agents": [a["agent"] for a in m.get("selected_agents", [])], "added_agents": added,
            "prod_fingerprint": fps["prod"],
            "failing": [c["name"] for c in res["checks"] if c["status"] != "PASS"],
            "count": len(res["checks"])}


def op_commit_integration(project: Path, run_id: str, scope: str, message: str) -> dict:
    m = sm.load_manifest(project, run_id)
    integ = Path(m["source"]["integration_worktree"])
    if m.get("isolation") == "read-only":
        return {"ok": True, "committed": False, "violations": []}
    res = wm.commit_integration(integ, message)
    pcfg = read_json(sm.dp_dir(project) / "config.json")
    allowed = {"qa": pcfg.get("qa_write_paths", []), "docs": DOC_SCOPE}.get(scope)
    violations = [f for f in res["changed_files"] if allowed is not None and not path_owned(f, allowed)]
    with sm.mutate(project, run_id) as mm:
        mm["source"]["integration_head"] = res["head"]
        if violations:
            mm.setdefault("ownership_violations", []).append({"scope": scope, "files": violations, "at": now_iso(), "resolved": False})
        elif scope == "integration":
            for v in mm.get("ownership_violations", []):
                if v["scope"] != "user-checkout":
                    v["resolved"] = True  # integration engineer reviewed and committed the integrated state
    return {"ok": True, "committed": res["committed"], "changed": len(res["changed_files"]), "violations": violations[:20],
            "head": res["head"]}


def op_validate_reports(project: Path, run_id: str, expect: list[str]) -> dict:
    m = sm.load_manifest(project, run_id)
    rd = sm.run_dir(project, run_id)
    fps = sm.fingerprints(project, m)
    missing, invalid, stale, ok = [], [], [], []
    for item in expect:
        kind, _, agent = item.partition(":")
        if kind in ("code_review", "final_review"):
            f = rd / "reviews" / f"{kind}.json"
            schema, fp = "review.schema.json", fps["code"]
        else:
            f = rd / "test-reports" / f"{kind}.json"
            schema, fp = "test-report.schema.json", fps["prod"]
        if not f.exists():
            missing.append(agent or kind)
            continue
        try:
            doc = read_json(f)
        except DPError as exc:
            invalid.append({"agent": agent, "error": str(exc)[:200]})
            continue
        errs = validate_named(doc, schema)
        if errs:
            invalid.append({"agent": agent, "error": errs[0][:200]})
        elif doc.get("source_fingerprint") != fp:
            stale.append(agent or kind)
        else:
            ok.append(agent or kind)
    return {"ok": True, "valid": ok, "missing": missing, "invalid": invalid, "stale": stale,
            "code_fingerprint": fps["code"], "prod_fingerprint": fps["prod"]}


def op_plan_repair(project: Path, run_id: str, iteration: int) -> dict:
    m = sm.load_manifest(project, run_id)
    cfg = m["config"]
    rd = sm.run_dir(project, run_id)
    gate = read_json(rd / m["gate_results"][-1]["path"]) if m["gate_results"] else None
    if gate is None:
        raise DPError("plan-repair requires a gate result")
    if iteration > cfg["max_repair_iterations"]:
        return {"ok": True, "stop": True, "status": "NEEDS_HUMAN_REVIEW",
                "reason": f"repair budget exhausted ({cfg['max_repair_iterations']} iterations)", "repair_tasks": []}
    issues = []
    for iid in gate["blocking_issue_ids"]:
        f = rd / "issues" / f"{iid}.json"
        if f.exists():
            issues.append(read_json(f))
    gates = {g["id"]: g for g in gate["gates"]}
    blocked_gates = [g for g in gate["gates"] if g["mandatory"] and g["status"] == "BLOCKED"]
    if blocked_gates and not issues:
        return {"ok": True, "stop": True, "status": "BLOCKED", "repair_tasks": [],
                "reason": "; ".join(f"{g['id']}: {g['reason']}" for g in blocked_gates)[:800]}
    if gate["pre_existing_only"]:
        return {"ok": True, "stop": True, "status": "NEEDS_HUMAN_REVIEW", "repair_tasks": [],
                "reason": "only pre-existing failures remain: " + ", ".join(gate["pre_existing_issue_ids"])}
    unsafe = [v for v in m.get("ownership_violations", []) if v["scope"] == "user-checkout" and not v.get("resolved")]
    if unsafe:
        return {"ok": True, "stop": True, "status": "NEEDS_HUMAN_REVIEW", "repair_tasks": [],
                "reason": "source state unsafe to modify: the user's checkout changed during the run ("
                          + ", ".join(unsafe[-1]["files"][:8]) + "); review it, then resume"}
    hist = m.get("issue_history", {})
    stalled_fps = issuemod.stalled(hist, [i["fingerprint"] for i in issues], iteration - 1, cfg["max_no_progress_iterations"])
    # documentation is regenerated by the workflow's DOCUMENTING stage once code gates pass
    issues = [i for i in issues if i["category"] != "documentation"]
    actionable = [i for i in issues if i["fingerprint"] not in stalled_fps]
    if issues and not actionable:
        with sm.mutate(project, run_id) as mm:
            mm["counters"]["no_progress_iterations"] += 1
        return {"ok": True, "stop": True, "status": "NEEDS_HUMAN_REVIEW", "repair_tasks": [],
                "reason": "no progress: issues unchanged after repeated repairs: " + ", ".join(i["id"] for i in issues)}
    if not actionable and len(m["gate_results"]) >= 2:
        prev = read_json(rd / m["gate_results"][-2]["path"])
        if sorted(prev.get("failing_mandatory", [])) == sorted(gate["failing_mandatory"]):
            return {"ok": True, "stop": True, "status": "NEEDS_HUMAN_REVIEW", "repair_tasks": [],
                    "reason": "no progress: the same mandatory gates keep failing without a repairable issue: "
                              + "; ".join(f"{g['id']}: {g['reason']}" for g in gate["gates"] if g["id"] in gate["failing_mandatory"])[:900]}
    if iteration > 1:
        sm.event(project, run_id, "REPAIR_COMPLETED", detail=f"iteration {iteration - 1} verified; gate {gate['overall']}")
    tasks = load_tasks(project, run_id)
    selected = {a["agent"] for a in m.get("selected_agents", [])}
    groups: dict[str, list[dict]] = {}
    for i in actionable:
        agent = i["assigned_agent"] if i["assigned_agent"] in selected or i["assigned_agent"] == "software-architect" else issuemod.route(i, tasks, selected)
        groups.setdefault(agent, []).append(i)
    new_tasks = []
    base_num = 900 + 10 * iteration
    arch_task_id = None
    if "software-architect" in groups:
        arch_task_id = f"TASK-{base_num:03d}-R{iteration}"
        new_tasks.append({"id": arch_task_id, "agent": "software-architect", "title": f"Resolve contract/requirement issues (repair {iteration})",
                          "kind": "contract", "requirements": sorted({r for i in groups['software-architect'] for r in i.get('requirement_ids', [])}),
                          "dependencies": [], "owned_paths": [], "acceptance_criteria": [], "status": "PENDING", "attempt": 0,
                          "issue_ids": [i["id"] for i in groups["software-architect"]], "repair_iteration": iteration,
                          "description": "Update contracts under .devpipeline/contracts and record the change in contract-changes.json."})
    for n, (agent, items) in enumerate(sorted((k, v) for k, v in groups.items() if k != "software-architect"), start=1):
        owned = sorted({p for t in tasks if t["agent"] == agent and not t["id"].endswith(f"-R{iteration}") for p in t.get("owned_paths", [])})
        for i in items:
            for f in i.get("affected_files", []):
                if not path_owned(f, owned) and agent in ("integration-engineer", "technical-writer"):
                    owned.append(f)
        if agent == "technical-writer":
            owned = sorted(set(owned) | {"README.md", "docs/", ".env.example"})
        if agent == "integration-engineer" and not owned:
            owned = sorted({p for t in tasks for p in t.get("owned_paths", []) + t.get("shared_paths", [])})
        if not owned:
            owned = sorted({f for i in items for f in i.get("affected_files", [])}) or ["."]
        new_tasks.append({"id": f"TASK-{base_num + n:03d}-R{iteration}", "agent": agent,
                          "title": f"Repair {len(items)} issue(s) (iteration {iteration})", "kind": "repair",
                          "requirements": sorted({r for i in items for r in i.get("requirement_ids", [])}),
                          "dependencies": [arch_task_id] if arch_task_id else [], "owned_paths": owned,
                          "shared_paths": sorted({p for t in tasks if t["agent"] == agent for p in t.get("shared_paths", [])}),
                          "acceptance_criteria": sorted({a for i in items for a in i.get("acceptance_criteria", [])}),
                          "status": "PENDING", "attempt": 0, "issue_ids": [i["id"] for i in items], "repair_iteration": iteration,
                          "description": "Fix the listed issues without weakening tests, removing requirements or changing contracts."})
    # serialise repair tasks with overlapping ownership
    from dpcore.dag import validate_tasks
    reg = cfgmod.registry()["agents"]
    chk = validate_tasks(new_tasks, set(reg), {k: v["role"] for k, v in reg.items()})
    if not chk["valid"]:
        raise DPError("invalid repair graph: " + "; ".join(chk["errors"]))
    for i in actionable:
        f = rd / "issues" / f"{i['id']}.json"
        doc = read_json(f)
        doc["attempts"] = doc.get("attempts", 0) + 1
        doc["status"] = "IN_REPAIR"
        atomic_write_json(f, doc)
    covered = {iid for t in new_tasks for iid in t.get("issue_ids", [])}
    for t in tasks:  # earlier failed repair attempts for issues that are re-planned now no longer block completion
        if t.get("kind") in ("repair", "contract") and t["status"] in ("FAILED", "BLOCKED") and set(t.get("issue_ids", [])) & covered:
            t["status"] = "CANCELLED"
            t["failure_reason"] = f"superseded by repair iteration {iteration}: {t.get('failure_reason', '')}"[:1000]
    tasks += new_tasks
    save_tasks(project, run_id, tasks)
    sync_task_map(project, run_id, tasks, conflicts=sorted(m.get("conflicts", []) + chk["conflicts"]))
    with sm.mutate(project, run_id) as mm:
        mm["counters"]["repair_iterations"] = iteration
    sm.event(project, run_id, "REPAIR_STARTED", detail=f"iteration {iteration}: {len(new_tasks)} task(s) for {len(actionable)} issue(s)")
    qa_rerun = sorted({a for a in selected if a in QA_KINDS})
    return {"ok": True, "stop": False, "iteration": iteration, "repair_tasks": [compact(t) for t in new_tasks],
            "conflicts": chk["conflicts"], "stalled": [i["id"] for i in issues if i["fingerprint"] in stalled_fps],
            "qa_rerun": qa_rerun,
            "code_review": "code-reviewer" in selected}


def op_select_audit(project: Path, run_id: str) -> dict:
    """Agent selection for audit/repair runs (no architect plan): detected features + profile rules."""
    m = sm.load_manifest(project, run_id)
    features = merge_features(m.get("features"), detect_features(Path(m["source"].get("integration_worktree") or project)))
    sel = select_agents(features, m["config"]["profile"], "audit" if m["mode"] == "audit" else m["mode"], None,
                        None, m["config"].get("agents"))
    with sm.mutate(project, run_id) as mm:
        mm["features"] = features
        mm["selected_agents"] = sel["selected"]
        mm["agent_decisions"] = sel["decisions"]
    for s_ in sel["selected"]:
        sm.event(project, run_id, "AGENT_SELECTED", agent=s_["agent"], detail=s_["reason"])
    selected = [x["agent"] for x in sel["selected"]]
    return {"ok": True, "features": features, "selected_agents": selected, "qa_agents": [a for a in selected if a in QA_KINDS],
            "errors": sel["errors"]}


def op_import_findings(project: Path, run_id: str, source_run: str, issue_ids: list[str]) -> dict:
    """Repair mode: copy approved findings from an earlier run and create scoped repair tasks."""
    if not issue_ids:
        raise DPError("repair mode needs an explicit, user-approved list of issue ids")
    src_dir = sm.run_dir(project, source_run) / "issues"
    if not src_dir.exists():
        raise DPError(f"source run {source_run} has no issues")
    m = sm.load_manifest(project, run_id)
    rd = sm.run_dir(project, run_id)
    imported = []
    for iid in issue_ids:
        f = src_dir / f"{iid}.json"
        if not f.exists():
            raise DPError(f"{iid} not found in {source_run}")
        issue = read_json(f)
        issue.update({"status": "OPEN", "attempts": 0, "first_seen_iteration": 0, "last_seen_iteration": 0})
        atomic_write_json(rd / "issues" / f"{iid}.json", issue)
        (rd / "issues" / f"{iid}.source").write_text("imported")
        imported.append(issue)
    index = {"next": max(int(i["id"].split("-")[1]) for i in imported) + 1,
             "by_fingerprint": {i["fingerprint"]: i["id"] for i in imported if i.get("fingerprint")}}
    atomic_write_json(rd / "issues" / "index.json", index)
    features = merge_features(m.get("features"), detect_features(Path(m["source"]["integration_worktree"])))
    impl_agents = sorted({i["assigned_agent"] for i in imported})
    sel = select_agents(features, m["config"]["profile"], "repair", impl_agents, impl_agents, m["config"].get("agents"))
    selected = {x["agent"] for x in sel["selected"]}
    groups: dict[str, list[dict]] = {}
    for i in imported:
        agent = i["assigned_agent"] if i["assigned_agent"] in cfgmod.registry()["repairable_agents"] else issuemod.route(i, [], selected)
        groups.setdefault(agent, []).append(i)
    tasks = []
    for n, (agent, items) in enumerate(sorted(groups.items()), start=1):
        owned = sorted({f for i in items for f in i.get("affected_files", [])})
        if not owned:
            raise DPError(f"issues {[i['id'] for i in items]} have no affected_files; repair scope would be unbounded")
        tasks.append({"id": f"TASK-{n:03d}", "agent": agent, "title": f"Repair approved finding(s) {', '.join(i['id'] for i in items)}",
                      "kind": "repair", "requirements": [], "dependencies": [], "owned_paths": owned,
                      "acceptance_criteria": [], "status": "PENDING", "attempt": 0, "issue_ids": [i["id"] for i in items],
                      "repair_iteration": 0, "description": "Fix only the approved findings; keep behaviour otherwise unchanged."})
    from dpcore.dag import validate_tasks
    reg = cfgmod.registry()["agents"]
    chk = validate_tasks(tasks, set(reg), {k: v["role"] for k, v in reg.items()})
    save_tasks(project, run_id, tasks)
    sync_task_map(project, run_id, tasks, features=features, selected_agents=sel["selected"], agent_decisions=sel["decisions"],
                  conflicts=chk["conflicts"], plan_validation={"valid": chk["valid"], "at": now_iso(), "source": f"repair of {source_run}"},
                  repair_scope={"source_run": source_run, "issue_ids": issue_ids})
    sm.event(project, run_id, "REPAIR_STARTED", detail=f"imported {len(imported)} approved finding(s) from {source_run}")
    return {"ok": True, "tasks": [compact(t) for t in tasks], "conflicts": chk["conflicts"],
            "selected_agents": sorted(selected), "qa_agents": [a for a in sorted(selected) if a in QA_KINDS]}


def _gate_report_kind(gid: str) -> str | None:
    return {"code_qa": "code_qa", "browser_qa": "browser_qa", "security_review": "security", "ai_evaluation": "ai_eval",
            "performance": "performance", "code_review": "code_review", "final_review": "final_review"}.get(gid)


def op_finalize(project: Path, run_id: str, status: str, reason: str) -> dict:
    if verify_user_checkout(project, run_id, "finalize") and status == "COMPLETED":
        status, reason = "NEEDS_HUMAN_REVIEW", "the user's checkout changed during the run; review before merging"
    m = sm.load_manifest(project, run_id)
    final = status
    note = reason
    if status == "COMPLETED":
        ok, why = sm.completion_allowed(project, m)
        if not ok:
            final, note = "NEEDS_HUMAN_REVIEW", f"completion refused by deterministic gate: {why}"
    if m["phase"] != final:
        if not sm.legal(m["phase"], final):
            # route through a legal intermediate where needed (e.g. REPAIRING -> NEEDS_HUMAN_REVIEW is legal)
            final = "NEEDS_HUMAN_REVIEW" if sm.legal(m["phase"], "NEEDS_HUMAN_REVIEW") else "FAILED"
        sm.transition(project, run_id, final, note)
    wt_info = {}
    if m.get("isolation") == "worktree":
        merged = [t["id"] for t in load_tasks(project, run_id) if t["status"] == "COMPLETED"]
        wt_info = wm.finalize(project, run_id, m["project_mode"], merged if final == "COMPLETED" else [], final == "COMPLETED")
    elif m.get("isolation") == "read-only":
        audit_wt = wm.wt_root(project, run_id) / "audit"
        if audit_wt.exists():
            gitutil.git(project, "worktree", "remove", "--force", str(audit_wt), check=False)  # DevPipeline-owned detached worktree
            wt_info = {"removed_audit_worktree": str(audit_wt)}
    with sm.mutate(project, run_id) as mm:
        mm["source"]["finalize"] = wt_info
    report = report_generator.generate(project, run_id)
    if active_path(project).exists():
        active_path(project).unlink()
    update_registry(project, run_id, False)
    return {"ok": True, "final_status": final, "reason": note, "report": report, "integration_branch": wt_info.get("integration_branch"),
            "fast_forwarded": wt_info.get("fast_forwarded", False), "merge_hint": wt_info.get("merge_hint")}


# ------------------------------------------------------------------ CLI ----
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command")
    ap.add_argument("--project")
    ap.add_argument("--run")
    ap.add_argument("--mode", choices=["build", "improve", "audit", "repair"])
    ap.add_argument("--request")
    ap.add_argument("--request-file")
    ap.add_argument("--profile", choices=["fast", "balanced", "strict"])
    ap.add_argument("--overrides-json")
    ap.add_argument("--new", action="store_true")
    ap.add_argument("--reuse-plan", action="store_true")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--approve-note")
    ap.add_argument("--to")
    ap.add_argument("--reason", default="")
    ap.add_argument("--step")
    ap.add_argument("--counters-b64")
    ap.add_argument("--tasks", default="")
    ap.add_argument("--returned-b64")
    ap.add_argument("--label", default="checks")
    ap.add_argument("--only")
    ap.add_argument("--scope", choices=["qa", "docs", "integration"])
    ap.add_argument("--message", default="devpipeline: integrate")
    ap.add_argument("--expect", default="")
    ap.add_argument("--iteration", type=int, default=1)
    ap.add_argument("--status")
    ap.add_argument("--awaiting", choices=["questions", "plan"])
    ap.add_argument("--op-key")
    ap.add_argument("--protocol", type=int)
    ap.add_argument("--relayed-b64")
    ap.add_argument("--source-run")
    ap.add_argument("--issues", default="")
    a = ap.parse_args(argv)
    try:
        if a.command == "version":
            return emit({"ok": True, "version": framework_version(), "root": str(ROOT)})
        if not a.project:
            raise DPError("--project is required")
        project = P(a.project)
        if a.command == "prepare":
            return emit(cmd_prepare(a))
        if a.command == "status":
            return emit(sm.status(project, a.run))
        if a.command == "resume":
            return emit(cmd_resume(a))
        if not a.run:
            raise DPError("--run is required")
        ids = [x for x in a.tasks.split(",") if x]
        sm.load_manifest(project, a.run)
        if a.protocol is not None and a.protocol != PROTOCOL:
            return emit({"ok": False, "protocol_mismatch": True, "error":
                         f"DevPipeline was updated while this run was in progress (workflow protocol {a.protocol}, helpers {PROTOCOL}). "
                         "Nothing was changed; resume the run to continue with the updated workflow."})
        legacy = a.protocol is None  # workflows from before the handshake expect full task lists
        if a.command == "relay-failure":
            return emit(op_relay_failure(project, a.run, a.op_key or "", decode_b64_json(a.relayed_b64, {})))
        cache = None
        if a.op_key:
            if not all(c.isalnum() or c in "-_" for c in a.op_key):
                raise DPError("invalid --op-key")
            cache = sm.run_dir(project, a.run) / "ops" / f"{a.op_key}.json"
            if cache.exists():  # a relay retry: return the recorded result, never execute an operation twice
                write_stdout(cache.read_text().strip())
                return 0
        seen = decode_b64_json(a.counters_b64, {}) if a.command != "transition" else {}
        with sm.mutate(project, a.run) as mm:
            mm["counters"]["operator_calls"] += 1
            for k, v in seen.items():
                if k in mm["counters"] and isinstance(v, int) and v > mm["counters"][k]:
                    mm["counters"][k] = v
        ops = {
            "context": lambda: op_context(project, a.run),
            "transition": lambda: op_transition(project, a.run, a),
            "validate-requirements": lambda: op_validate_requirements(project, a.run),
            "validate-plan": lambda: op_validate_plan(project, a.run),
            "baseline": lambda: op_baseline(project, a.run),
            "start-tasks": lambda: op_start_tasks(project, a.run, ids),
            "finish-tasks": lambda: op_finish_tasks(project, a.run, ids, decode_b64_json(a.returned_b64, {})),
            "verify-merge": lambda: op_verify_merge(project, a.run, ids),
            "integration-status": lambda: op_integration_status(project, a.run),
            "run-checks": lambda: op_run_checks(project, a.run, a.label, a.only),
            "commit-integration": lambda: op_commit_integration(project, a.run, a.scope or "integration", a.message),
            "validate-reports": lambda: op_validate_reports(project, a.run, [x for x in a.expect.split(",") if x]),
            "gate": lambda: quality_gate.evaluate(project, a.run),
            "plan-repair": lambda: op_plan_repair(project, a.run, a.iteration),
            "select-audit": lambda: op_select_audit(project, a.run),
            "import-findings": lambda: op_import_findings(project, a.run, a.source_run, [x for x in a.issues.split(",") if x]),
            "finalize": lambda: op_finalize(project, a.run, a.status or "NEEDS_HUMAN_REVIEW", a.reason),
        }
        if a.command not in ops:
            raise DPError(f"unknown command '{a.command}'")
        result = ops[a.command]()
        if legacy and a.command in ("finish-tasks", "verify-merge"):
            result["tasks"] = [compact(t) for t in load_tasks(project, a.run)]
        line = emit_line(result)
        if cache is not None:
            atomic_write_text(cache, line + "\n")
        write_stdout(line)
        return 0
    except DPError as exc:
        return emit({"ok": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
