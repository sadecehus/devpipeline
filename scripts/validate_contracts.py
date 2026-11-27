#!/usr/bin/env python3
"""Validate planning contracts and agent outputs.

  validate_contracts.py requirements --file .devpipeline/requirements.json
  validate_contracts.py architecture --file .devpipeline/architecture.json
  validate_contracts.py tasks --file .devpipeline/tasks.json [--requirements FILE]
  validate_contracts.py result --file agent-results/TASK-001.json [--task ID --agent NAME --run ID]
  validate_contracts.py report --file test-reports/x.json        (test-report schema)
  validate_contracts.py review --file reviews/final_review.json  (review schema)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dpcore import config as cfgmod  # noqa: E402
from dpcore.dag import validate_tasks  # noqa: E402
from dpcore.jsonschema_lite import validate_named  # noqa: E402
from dpcore.util import DPError, emit, read_json  # noqa: E402


def check_requirements(req: dict) -> dict:
    errors = validate_named(req, "requirements.schema.json")
    warnings: list[str] = []
    if not errors:
        rids = [r["id"] for r in req["requirements"]]
        acids = [a["id"] for r in req["requirements"] for a in r["acceptance_criteria"]]
        for label, ids in (("requirement", rids), ("acceptance criterion", acids)):
            dup = sorted({i for i in ids if ids.count(i) > 1})
            if dup:
                errors.append(f"duplicate {label} ids: {', '.join(dup)}")
        if not any(r["priority"] == "must" for r in req["requirements"]):
            warnings.append("no 'must' requirement defined")
        for r in req["requirements"]:
            for ac in r["acceptance_criteria"]:
                if len(ac["description"].split()) < 3:
                    warnings.append(f"{ac['id']}: acceptance criterion is not measurable enough")
    return {"valid": not errors, "errors": errors, "warnings": warnings,
            "blocking_questions": req.get("blocking_questions", []) if not errors else [],
            "counts": {"requirements": len(req.get("requirements", [])),
                       "acceptance_criteria": sum(len(r.get("acceptance_criteria", [])) for r in req.get("requirements", []))}}


def check_architecture(arch: dict, req: dict | None = None) -> dict:
    errors = validate_named(arch, "architecture.schema.json")
    warnings: list[str] = []
    known = cfgmod.known_agents()
    if not errors:
        for a in arch["required_agents"]:
            if a not in known:
                errors.append(f"required_agents: unknown agent '{a}'")
        for o in arch["ownership"]:
            if o["agent"] not in known:
                errors.append(f"ownership: unknown agent '{o['agent']}'")
        prefs = (req or {}).get("stack_preferences", {})
        for k, v in prefs.items():
            chosen = str(arch["stack"].get(k) or "")
            if v and v.lower() not in chosen.lower() and not arch.get("stack_change_justification"):
                errors.append(f"stack '{k}': user asked for '{v}' but architecture chose '{chosen}' without stack_change_justification")
        for env in arch.get("env_vars", []):
            if env.get("secret") and env.get("example") and env["example"] not in ("", "<secret>", "changeme"):
                errors.append(f"env var {env['name']}: secret example values are not allowed")
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def check_tasks(tasks_doc: dict, req: dict | None = None) -> dict:
    errors = validate_named(tasks_doc, "task.schema.json")
    if errors:
        return {"valid": False, "errors": errors, "warnings": [], "conflicts": [], "order": []}
    reg = cfgmod.registry()["agents"]
    roles = {k: v["role"] for k, v in reg.items()}
    rids = acids = None
    if req:
        rids = {r["id"] for r in req["requirements"]}
        acids = {a["id"] for r in req["requirements"] for a in r["acceptance_criteria"]}
    res = validate_tasks(tasks_doc["tasks"], set(reg), roles, rids, acids)
    if req and res["valid"]:
        covered = {r for t in tasks_doc["tasks"] for r in t.get("requirements", [])}
        for r in req["requirements"]:
            if r["priority"] in ("must", "should") and r["id"] not in covered:
                res["errors"].append(f"{r['id']} ({r['priority']}) is not covered by any task")
        res["valid"] = not res["errors"]
    return res


def check_result(result: dict | None, task_id: str | None = None, agent: str | None = None,
                 run_id: str | None = None) -> dict:
    if result is None:
        return {"valid": False, "errors": ["missing agent result (null/absent) — never counted as success"]}
    errors = validate_named(result, "agent-result.schema.json")
    if not errors:
        if task_id and result["task_id"] != task_id:
            errors.append(f"task_id mismatch: expected {task_id}, got {result['task_id']}")
        if agent and result["agent"] != agent:
            errors.append(f"agent mismatch: expected {agent}, got {result['agent']}")
        if run_id and result["run_id"] != run_id:
            errors.append(f"run_id mismatch: expected {run_id}, got {result['run_id']}")
        if result["agent"] not in cfgmod.known_agents():
            errors.append(f"unknown agent '{result['agent']}'")
        for v in result["validation"]:
            if v["status"] == "PASS" and v.get("exit_code") not in (0, None):
                errors.append(f"validation '{v['name']}' claims PASS with exit code {v['exit_code']}")
            if v["status"] == "PASS" and v.get("exit_code") is None and not v.get("evidence_path"):
                errors.append(f"validation '{v['name']}' claims PASS without exit code or evidence")
        for art in result.get("artifacts", []):
            if ".." in Path(art["path"]).parts:
                errors.append(f"invalid artifact path {art['path']} (path traversal)")
    return {"valid": not errors, "errors": errors}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["requirements", "architecture", "tasks", "result", "report", "review"])
    ap.add_argument("--file", required=True)
    ap.add_argument("--requirements")
    ap.add_argument("--task")
    ap.add_argument("--agent")
    ap.add_argument("--run")
    a = ap.parse_args(argv)
    try:
        path = Path(a.file)
        doc = read_json(path) if path.exists() else None
        req = read_json(a.requirements) if a.requirements else None
        if a.kind == "result":
            out = check_result(doc, a.task, a.agent, a.run)
        elif doc is None:
            out = {"valid": False, "errors": [f"missing file {path}"]}
        elif a.kind == "requirements":
            out = check_requirements(doc)
        elif a.kind == "architecture":
            out = check_architecture(doc, req)
        elif a.kind == "tasks":
            out = check_tasks(doc, req)
        elif a.kind == "report":
            errs = validate_named(doc, "test-report.schema.json")
            out = {"valid": not errs, "errors": errs}
        else:
            errs = validate_named(doc, "review.schema.json")
            out = {"valid": not errs, "errors": errs}
        return emit({"ok": True, **out}, 0 if out["valid"] else 1)
    except DPError as exc:
        return emit({"ok": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
