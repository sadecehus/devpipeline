#!/usr/bin/env python3
"""Persistent, coordinator-owned pipeline state.

Only this module writes `.devpipeline/state.json`, run manifests, events and
checkpoints. Every write happens under an fcntl lock with atomic replace, so
parallel agents can never corrupt shared state (the hook guard additionally
denies direct agent writes to these files).

CLI (all commands print one JSON object):
  state_manager.py status  --project DIR [--run ID]
  state_manager.py transition --project DIR --run ID --to PHASE [--reason TEXT]
  state_manager.py checkpoint --project DIR --run ID --label TEXT
  state_manager.py resume-check --project DIR [--run ID]
  state_manager.py approve --project DIR --run ID [--note TEXT]
  state_manager.py cancel --project DIR --run ID [--reason TEXT]
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dpcore import framework_version  # noqa: E402
from dpcore import config as cfgmod  # noqa: E402
from dpcore import gitutil  # noqa: E402
from dpcore.jsonschema_lite import validate_named  # noqa: E402
from dpcore.util import DPError, atomic_write_json, emit, file_lock, new_run_id, now_iso, read_json  # noqa: E402
from redact_secrets import redact  # noqa: E402

RUN_SUBDIRS = ["agent-results", "test-reports", "issues", "screenshots", "logs", "artifacts", "reviews", "gates"]
COORDINATOR_FILES = ["state.json", "active.json", "lock"]


# ---------------------------------------------------------------- paths ----
def dp_dir(project: Path) -> Path:
    return Path(project).resolve() / ".devpipeline"


def run_dir(project: Path, run_id: str) -> Path:
    return dp_dir(project) / "runs" / run_id


def lock_path(project: Path) -> Path:
    return dp_dir(project) / "lock"


# ---------------------------------------------------------- project init ----
def ensure_project(project: Path, name: str | None = None, created_by_devpipeline: bool = False,
                   profile: str | None = None, language: str | None = None) -> dict:
    project = Path(project).resolve()
    d = dp_dir(project)
    (d / "runs").mkdir(parents=True, exist_ok=True)
    (d / "checkpoints").mkdir(exist_ok=True)
    (d / "contracts").mkdir(exist_ok=True)
    (d / "adr").mkdir(exist_ok=True)
    cfg_path = d / "config.json"
    with file_lock(lock_path(project)):
        if cfg_path.exists():
            pcfg = read_json(cfg_path)
        else:
            pcfg = {"schema_version": 1, "project_id": str(uuid.uuid4()), "project_name": name or project.name,
                    "created_by_devpipeline": created_by_devpipeline, "profile": profile or "balanced",
                    "language": language or "en", "stack_preferences": {}, "overrides": {}, "protected_paths": [],
                    "qa_write_paths": ["tests/", "test/", "e2e/", "evals/", "**/__tests__/**", "*.test.*", "*.spec.*", "**/*.test.*",
                                       "**/*.spec.*", "**/tests/**", "**/e2e/**", "playwright.config.*", "**/playwright.config.*"],
                    "integrations": {}, "approved_environments": ["http://localhost", "http://127.0.0.1"]}
            atomic_write_json(cfg_path, pcfg)
        errs = validate_named(pcfg, "project.schema.json")
        if errs:
            raise DPError("invalid .devpipeline/config.json: " + "; ".join(errs))
        st_path = d / "state.json"
        if not st_path.exists():
            atomic_write_json(st_path, {"schema_version": 1, "project_id": pcfg["project_id"], "project_dir": str(project),
                                        "framework_version": framework_version(), "current_run_id": None, "runs": []})
    _ensure_gitignore(project, bool(pcfg.get("created_by_devpipeline")))
    return pcfg


def _ensure_gitignore(project: Path, created_by_devpipeline: bool = False) -> None:
    """Keep .devpipeline/ out of git.

    Projects DevPipeline creates get a .gitignore entry. Existing repositories get
    the entry in .git/info/exclude instead, so no user-visible file is modified.
    """
    entry = ".devpipeline/"
    git_dir = project / ".git"
    if git_dir.is_dir() and not created_by_devpipeline:
        exclude = git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        lines = exclude.read_text().splitlines() if exclude.exists() else []
        if entry not in lines:
            exclude.write_text("\n".join(lines + ["# DevPipeline execution state", entry]) + "\n")
        return
    gi = project / ".gitignore"
    lines = gi.read_text().splitlines() if gi.exists() else []
    if entry not in lines and ".devpipeline" not in lines:
        lines += ["", "# DevPipeline execution state (local, may contain logs)", entry]
        gi.write_text("\n".join(lines).lstrip("\n") + "\n")


# ------------------------------------------------------------- run store ----
def load_manifest(project: Path, run_id: str) -> dict:
    return read_json(run_dir(project, run_id) / "manifest.json")


@contextmanager
def mutate(project: Path, run_id: str) -> Iterator[dict]:
    """Locked read-modify-write of a run manifest (atomic replace on success)."""
    path = run_dir(project, run_id) / "manifest.json"
    with file_lock(lock_path(project)):
        manifest = read_json(path)
        yield manifest
        manifest["updated_at"] = now_iso()
        errs = validate_named(manifest, "state.schema.json", "/$defs/run")
        if errs:
            raise DPError("refusing to persist invalid manifest: " + "; ".join(errs[:5]))
        atomic_write_json(path, manifest)


def _update_state_index(project: Path, run_id: str, **fields) -> None:
    path = dp_dir(project) / "state.json"
    st = read_json(path)
    for r in st["runs"]:
        if r["run_id"] == run_id:
            r.update(fields)
    atomic_write_json(path, st)


def event(project: Path, run_id: str, etype: str, phase: str | None = None, agent: str | None = None,
          task_id: str | None = None, status: str | None = None, duration_ms: int | None = None,
          artifacts: list[str] | None = None, detail: str | None = None) -> dict:
    rec = {"run_id": run_id, "ts": now_iso(), "event": etype}
    for k, v in (("phase", phase), ("agent", agent), ("task_id", task_id), ("status", status),
                 ("duration_ms", duration_ms), ("artifacts", artifacts), ("detail", detail)):
        if v is not None:
            rec[k] = v
    line = redact(json.dumps(rec, ensure_ascii=False))
    path = run_dir(project, run_id) / "events.jsonl"
    with file_lock(dp_dir(project) / "events.lock"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return rec


def create_run(project: Path, mode: str, request: str, profile: str | None = None,
               cli_overrides: dict | None = None, project_mode: str = "existing") -> dict:
    project = Path(project).resolve()
    pcfg = ensure_project(project)
    config = cfgmod.resolve(profile or pcfg.get("profile"), pcfg.get("overrides"), cli_overrides)
    run_id = new_run_id()
    rd = run_dir(project, run_id)
    for sub in RUN_SUBDIRS:
        (rd / sub).mkdir(parents=True, exist_ok=True)
    request = redact(request)  # never persist credentials a user may paste into a request
    (rd / "request.txt").write_text(request, encoding="utf-8")
    manifest = {
        "run_id": run_id, "mode": mode, "project_mode": project_mode, "phase": "INITIALIZING",
        "original_request": request, "framework_version": framework_version(), "started_at": now_iso(),
        "ended_at": None, "project_id": pcfg["project_id"], "project_dir": str(project), "config": config,
        "phase_history": [], "completed_steps": [],
        "counters": {"agent_calls": 0, "operator_calls": 0, "repair_iterations": 0, "no_progress_iterations": 0},
        "tasks": {}, "selected_agents": [], "agent_decisions": [], "source": {
            "base_revision": gitutil.head(project) if gitutil.is_repo(project) else None},
        "gate_results": [], "issue_history": {}, "open_issues": [], "resolved_issues": [], "checkpoints": [],
        "approvals": [], "final_status": None, "stop_reason": None,
        "usage": {"note": "Token/cost usage is not observable from workflow scripts except budget.spent(); only counters are recorded."},
    }
    errs = validate_named(manifest, "state.schema.json", "/$defs/run")
    if errs:
        raise DPError("; ".join(errs))
    with file_lock(lock_path(project)):
        atomic_write_json(rd / "manifest.json", manifest)
        st = read_json(dp_dir(project) / "state.json")
        st["current_run_id"] = run_id
        st["framework_version"] = framework_version()
        st["runs"].append({"run_id": run_id, "mode": mode, "status": "INITIALIZING", "started_at": manifest["started_at"], "ended_at": None})
        atomic_write_json(dp_dir(project) / "state.json", st)
    event(project, run_id, "RUN_STARTED", phase="INITIALIZING", detail=f"mode={mode} profile={config['profile']}")
    return manifest


def fingerprints(project: Path, manifest: dict) -> dict:
    """code = all non-doc files (check/review evidence); prod = code minus QA test paths (QA report evidence)."""
    wt = Path(manifest.get("source", {}).get("integration_worktree") or manifest["project_dir"])
    pcfg = read_json(dp_dir(project) / "config.json", default={})
    qa = pcfg.get("qa_write_paths", [])
    return {"code": gitutil.tree_fingerprint(wt), "prod": gitutil.tree_fingerprint(wt, extra_excludes=qa),
            "docs": gitutil.tree_fingerprint(wt, include_docs=True)}


# ----------------------------------------------------------- transitions ----
def legal(frm: str, to: str) -> bool:
    return to in cfgmod.state_machine()["transitions"].get(frm, [])


def completion_allowed(project: Path, manifest: dict) -> tuple[bool, str]:
    """COMPLETED requires the most recent gate to pass for the current fingerprint."""
    gates = manifest.get("gate_results") or []
    if not gates:
        return False, "no quality gate result recorded"
    last = gates[-1]
    gate_file = run_dir(project, manifest["run_id"]) / last["path"]
    if not gate_file.exists():
        return False, "gate result file missing"
    gate = read_json(gate_file)
    if not gate.get("passed"):
        return False, f"last quality gate did not pass ({gate.get('overall')})"
    wt = manifest.get("source", {}).get("integration_worktree") or manifest["project_dir"]
    current = gitutil.tree_fingerprint(Path(wt))
    if gate.get("code_fingerprint") != current:
        return False, "code changed after the last passing gate (stale evidence)"
    return True, "gate passed for current fingerprint"


def transition(project: Path, run_id: str, to: str, reason: str = "", counters: dict | None = None,
               step: str | None = None) -> dict:
    project = Path(project).resolve()
    with mutate(project, run_id) as m:
        frm = m["phase"]
        if counters:
            for k, v in counters.items():
                if k in m["counters"] and isinstance(v, int) and v >= m["counters"][k]:
                    m["counters"][k] = v
        if step and step not in m["completed_steps"]:
            m["completed_steps"].append(step)
        limit = (m.get("config") or {}).get("max_runtime_minutes")
        if limit and to not in cfgmod.state_machine()["terminal"] and to not in ("BLOCKED", "NEEDS_HUMAN_REVIEW", "BUDGET_EXHAUSTED"):
            from dpcore.util import parse_iso
            import datetime as _dt
            elapsed = (_dt.datetime.now(_dt.timezone.utc) - parse_iso(m["started_at"])).total_seconds() / 60
            if elapsed > limit:
                raise DPError(f"max_runtime_minutes ({limit}) exceeded after {elapsed:.0f} min — stop the run (BUDGET_EXHAUSTED)")
        if to == frm:
            return {"ok": True, "phase": frm, "changed": False}
        if not legal(frm, to):
            raise DPError(f"illegal transition {frm} -> {to}")
        if to == "COMPLETED":
            ok, why = completion_allowed(project, m)
            if not ok:
                raise DPError(f"COMPLETED refused: {why}")
        m["phase"] = to
        m["phase_history"].append({"from": frm, "to": to, "at": now_iso(), "reason": reason})
        if to in cfgmod.state_machine()["terminal"] or to in ("BLOCKED", "NEEDS_HUMAN_REVIEW", "BUDGET_EXHAUSTED"):
            m["final_status"] = to
            m["ended_at"] = now_iso()
            m["stop_reason"] = reason or m.get("stop_reason")
        _update_state_index(project, run_id, status=to, ended_at=m.get("ended_at"))
    etype = {"COMPLETED": "RUN_COMPLETED", "BLOCKED": "RUN_BLOCKED", "NEEDS_HUMAN_REVIEW": "RUN_BLOCKED",
             "BUDGET_EXHAUSTED": "RUN_BLOCKED", "FAILED": "RUN_BLOCKED", "CANCELLED": "RUN_BLOCKED"}.get(to, "PHASE_CHANGED")
    event(project, run_id, etype, phase=to, status=to, detail=f"{frm} -> {to}: {reason}")
    checkpoint(project, run_id, f"{frm}-to-{to}")
    return {"ok": True, "phase": to, "from": frm, "changed": True}


def checkpoint(project: Path, run_id: str, label: str) -> str:
    project = Path(project).resolve()
    rd = run_dir(project, run_id)
    cdir = dp_dir(project) / "checkpoints" / run_id
    cdir.mkdir(parents=True, exist_ok=True)
    with file_lock(lock_path(project)):
        m = read_json(rd / "manifest.json")
        seq = len(m.get("checkpoints", [])) + 1
        name = f"{seq:03d}-{''.join(c if c.isalnum() or c in '-_' else '-' for c in label)[:60]}.json"
        tasks = read_json(rd / "tasks.json", default={"tasks": []}) if (rd / "tasks.json").exists() else {"tasks": []}
        atomic_write_json(cdir / name, {"created_at": now_iso(), "label": label, "manifest": m, "tasks": tasks})
        m["checkpoints"].append(name)
        atomic_write_json(rd / "manifest.json", m)
    event(project, run_id, "CHECKPOINT_CREATED", detail=name)
    return name


# --------------------------------------------------------------- resume ----
def latest_run(project: Path) -> str | None:
    st = read_json(dp_dir(project) / "state.json")
    return st.get("current_run_id")


def resume_check(project: Path, run_id: str | None = None) -> dict:
    """Validate that an interrupted run can resume and prepare its state."""
    project = Path(project).resolve()
    pcfg = read_json(dp_dir(project) / "config.json")
    run_id = run_id or latest_run(project)
    if not run_id:
        raise DPError("no run to resume")
    m = load_manifest(project, run_id)
    problems: list[str] = []
    if m.get("project_id") != pcfg["project_id"]:
        problems.append("project identity mismatch (manifest belongs to another project)")
    if m["phase"] in cfgmod.state_machine()["terminal"]:
        problems.append(f"run already terminal ({m['phase']})")
    src = m.get("source", {})
    external = []
    wt = src.get("integration_worktree")
    if wt and Path(wt).exists() and src.get("integration_head"):
        actual = gitutil.head(Path(wt))
        if actual != src["integration_head"]:
            external.append(f"integration branch moved: recorded {src['integration_head'][:10]} actual {str(actual)[:10]}")
        if gitutil.status_porcelain(Path(wt)):
            external.append("uncommitted changes in integration worktree")
    elif wt and not Path(wt).exists():
        problems.append(f"integration worktree missing: {wt}")
    if problems:
        return {"ok": False, "run_id": run_id, "phase": m["phase"], "problems": problems, "external_changes": external,
                "reset_tasks": [], "stale_reports": [], "completed_steps": m.get("completed_steps", []), "mode": m["mode"]}
    reset = []
    with mutate(project, run_id) as mm:
        for tid, t in mm["tasks"].items():
            if t["status"] == "RUNNING":
                t["status"] = "PENDING"
                reset.append(tid)
        if external:
            mm.setdefault("source", {})["external_changes"] = external
    tasks_path = run_dir(project, run_id) / "tasks.json"
    if reset and tasks_path.exists():
        with file_lock(lock_path(project)):
            td = read_json(tasks_path)
            for t in td["tasks"]:
                if t["id"] in reset:
                    t["status"] = "PENDING"
            atomic_write_json(tasks_path, td)
    stale = _mark_stale_reports(project, run_id)
    event(project, run_id, "RESUME_CHECKED", phase=m["phase"], detail=f"reset={reset} external={external} stale={len(stale)}")
    return {"ok": not problems, "run_id": run_id, "phase": m["phase"], "problems": problems,
            "external_changes": external, "reset_tasks": reset, "stale_reports": stale,
            "completed_steps": m.get("completed_steps", []), "mode": m["mode"]}


def _mark_stale_reports(project: Path, run_id: str) -> list[str]:
    """Flag reports whose fingerprint differs from the current integration tree."""
    m = load_manifest(project, run_id)
    wt = m.get("source", {}).get("integration_worktree")
    if not wt or not Path(wt).exists():
        return []
    current = gitutil.tree_fingerprint(Path(wt))
    stale = []
    for f in sorted((run_dir(project, run_id) / "test-reports").glob("*.json")) + \
            sorted((run_dir(project, run_id) / "reviews").glob("*.json")):
        try:
            rep = read_json(f)
        except DPError:
            continue
        if rep.get("source_fingerprint") and rep["source_fingerprint"] != current and not rep.get("stale"):
            rep["stale"] = True
            atomic_write_json(f, rep)
            stale.append(f.name)
    return stale


def approve(project: Path, run_id: str, note: str = "") -> dict:
    """Record an approval of the kind the run is waiting for ("questions" or "plan")."""
    with mutate(project, run_id) as m:
        kind = m.get("awaiting") or "general"
        m["approvals"].append({"at": now_iso(), "phase": m["phase"], "kind": kind, "note": note})
        m["awaiting"] = None
    event(project, run_id, "APPROVAL_RECORDED", detail=note)
    return {"ok": True, "run_id": run_id}


def status(project: Path, run_id: str | None = None) -> dict:
    project = Path(project).resolve()
    if not (dp_dir(project) / "state.json").exists():
        return {"ok": False, "error": "not a DevPipeline project (no .devpipeline/state.json)"}
    run_id = run_id or latest_run(project)
    if not run_id:
        return {"ok": True, "runs": 0, "message": "no runs yet"}
    m = load_manifest(project, run_id)
    by_status: dict[str, list[str]] = {}
    for tid, t in sorted(m["tasks"].items()):
        by_status.setdefault(t["status"], []).append(tid)
    gate = m["gate_results"][-1] if m["gate_results"] else None
    gate_detail = None
    if gate:
        g = read_json(run_dir(project, run_id) / gate["path"])
        gate_detail = {x["id"]: x["status"] for x in g.get("gates", [])}
    issues_dir = run_dir(project, run_id) / "issues"
    open_blocking = []
    for f in sorted(issues_dir.glob("ISSUE-*.json")):
        i = read_json(f)
        if i.get("status") == "OPEN" and i.get("blocking"):
            open_blocking.append({"id": i["id"], "severity": i["severity"], "assigned_agent": i["assigned_agent"], "title": i.get("title") or i["description"][:80]})
    return {"ok": True, "run_id": run_id, "mode": m["mode"], "phase": m["phase"], "profile": m["config"]["profile"],
            "started_at": m["started_at"], "ended_at": m.get("ended_at"), "tasks": by_status,
            "selected_agents": [a["agent"] for a in m.get("selected_agents", [])],
            "repair_iteration": m["counters"]["repair_iterations"], "counters": m["counters"],
            "open_blocking_issues": open_blocking, "quality_gate": gate_detail,
            "gate_passed": bool(gate and read_json(run_dir(project, run_id) / gate["path"]).get("passed")),
            "stop_reason": m.get("stop_reason"),
            "usage_note": "Token/cost usage is not observable; agent and operator call counters are exact.",
            "report": str(run_dir(project, run_id) / "final-report.md")}


# ------------------------------------------------------------------ CLI ----
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "transition", "checkpoint", "resume-check", "approve", "cancel"])
    ap.add_argument("--project", required=True)
    ap.add_argument("--run")
    ap.add_argument("--to")
    ap.add_argument("--reason", default="")
    ap.add_argument("--label", default="manual")
    ap.add_argument("--note", default="")
    a = ap.parse_args(argv)
    project = Path(a.project)
    try:
        if a.command == "status":
            return emit(status(project, a.run))
        if a.command == "transition":
            return emit(transition(project, a.run, a.to, a.reason))
        if a.command == "checkpoint":
            return emit({"ok": True, "checkpoint": checkpoint(project, a.run, a.label)})
        if a.command == "resume-check":
            return emit(resume_check(project, a.run))
        if a.command == "approve":
            return emit(approve(project, a.run, a.note))
        if a.command == "cancel":
            return emit(transition(project, a.run, "CANCELLED", a.reason or "cancelled by user"))
    except DPError as exc:
        return emit({"ok": False, "error": str(exc)}, 1)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
