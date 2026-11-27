"""Issue normalisation, fingerprinting, de-duplication, routing and stall detection."""
from __future__ import annotations

import hashlib
import re

from . import config as cfgmod
from .dag import path_owned

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]
_WS = re.compile(r"\s+")
_NUM = re.compile(r"\b\d+\b")


def fingerprint(issue: dict) -> str:
    text = (issue.get("title") or "") + " " + (issue.get("description") or "")
    text = _NUM.sub("#", _WS.sub(" ", text.lower())).strip()[:160]
    files = ",".join(sorted(set(issue.get("affected_files") or [])))
    key = "|".join([issue.get("category", ""), issue.get("check_name", ""), files, text])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def normalize(issue: dict, source_agent: str | None = None) -> dict:
    out = dict(issue)
    out.setdefault("source_agent", source_agent or "pipeline-operator")
    out.setdefault("severity", "medium")
    out.setdefault("category", "functional")
    out.setdefault("requirement_ids", [])
    out.setdefault("affected_files", [])
    out.setdefault("evidence", [])
    out.setdefault("reproduction_steps", [])
    out.setdefault("status", "OPEN")
    out.setdefault("attempts", 0)
    if "blocking" not in out:
        out["blocking"] = out["severity"] in ("critical", "high")
    if out["category"] in ("security", "ai_security") and out["severity"] in ("critical", "high"):
        out["blocking"] = True  # critical/high security findings block by default
    out["affected_files"] = sorted({f[2:] if f.startswith("./") else f for f in out["affected_files"]})
    out["fingerprint"] = fingerprint(out)
    return out


def dedupe(issues: list[dict]) -> list[dict]:
    by_fp: dict[str, dict] = {}
    for issue in issues:
        fp = issue["fingerprint"]
        if fp not in by_fp:
            by_fp[fp] = dict(issue)
            continue
        cur = by_fp[fp]
        if SEVERITY_ORDER.index(issue["severity"]) > SEVERITY_ORDER.index(cur["severity"]):
            cur["severity"] = issue["severity"]
        cur["blocking"] = cur["blocking"] or issue["blocking"]
        for key in ("requirement_ids", "evidence", "affected_files", "reproduction_steps"):
            cur[key] = sorted(set(cur.get(key, [])) | set(issue.get(key, [])))
        srcs = set(cur.get("source_agent", "").split("+")) | {issue.get("source_agent", "")}
        cur["source_agent"] = "+".join(sorted(s for s in srcs if s))
    return sorted(by_fp.values(), key=lambda i: (-SEVERITY_ORDER.index(i["severity"]), i["fingerprint"]))


def owner_of(files: list[str], tasks: list[dict]) -> str | None:
    for f in files:
        for t in sorted(tasks, key=lambda x: x["id"]):
            if path_owned(f, t.get("owned_paths", []) + t.get("shared_paths", [])):
                return t["agent"]
    return None


def route(issue: dict, tasks: list[dict], selected: set[str]) -> str:
    """Deterministically choose the responsible agent for an issue."""
    reg = cfgmod.registry()
    repairable = set(reg["repairable_agents"]) | {"software-architect"}
    proposed = issue.get("assigned_agent")
    if proposed in repairable and proposed in selected and proposed not in ("software-architect",):
        return proposed
    target = reg["routing"].get(issue.get("category", ""), "$owner")
    if target == "$owner":
        target = owner_of(issue.get("affected_files", []), tasks) or proposed or ""
    seen = set()
    while target and target not in selected and target not in seen:
        seen.add(target)
        target = reg["routing_fallbacks"].get(target, "")
    if target in selected and target in repairable:
        return target
    for fallback in ("backend-engineer", "frontend-engineer", "ai-engineer", "integration-engineer"):
        if fallback in selected:
            return fallback
    return "integration-engineer"


def update_history(history: dict, open_fps: list[str], iteration: int) -> dict:
    """history: fingerprint -> list of iterations the issue was observed open."""
    for fp in open_fps:
        seen = history.setdefault(fp, [])
        if iteration not in seen:
            seen.append(iteration)
    return history


def stalled(history: dict, open_fps: list[str], iteration: int, limit: int) -> list[str]:
    """An issue is stalled when it stayed open through `limit` consecutive repair iterations."""
    out = []
    for fp in open_fps:
        seen = sorted(history.get(fp, []))
        window = list(range(iteration - limit, iteration + 1))
        if iteration >= limit and all(i in seen for i in window):
            out.append(fp)
    return out
