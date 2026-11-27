"""Task dependency graph validation: ids, agents, dependencies, cycles, ownership."""
from __future__ import annotations

import fnmatch
from itertools import combinations
from typing import Iterable

WRITING_ROLES = {"implementation", "integration", "documentation"}


def norm_path(p: str) -> str:
    p = p.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _static_prefix(pattern: str) -> str:
    for i, ch in enumerate(pattern):
        if ch in "*?[":
            return pattern[:i]
    return pattern


def paths_overlap(a: str, b: str) -> bool:
    """Conservative overlap test between two ownership entries.

    'dir/' owns everything below it; globs are compared by their static prefix
    and by fnmatch in both directions.
    """
    a, b = norm_path(a), norm_path(b)
    if not a or not b:
        return True
    if a == b:
        return True
    if any(c in a for c in "*?[") or any(c in b for c in "*?["):
        if fnmatch.fnmatch(b, a) or fnmatch.fnmatch(a, b):
            return True
        pa, pb = _static_prefix(a), _static_prefix(b)
        return pa.startswith(pb) or pb.startswith(pa)
    da = a if a.endswith("/") else a + "/"
    db = b if b.endswith("/") else b + "/"
    return db.startswith(da) or da.startswith(db)


def path_owned(rel_path: str, owned: Iterable[str]) -> bool:
    rel = norm_path(rel_path)
    for entry in owned:
        e = norm_path(entry)
        if not e:
            continue
        if any(c in e for c in "*?["):
            if fnmatch.fnmatch(rel, e) or fnmatch.fnmatch(rel, e.rstrip("/") + "/*"):
                return True
            continue
        if rel == e.rstrip("/") or rel.startswith(e if e.endswith("/") else e + "/"):
            return True
    return False


def find_cycle(tasks: list[dict]) -> list[str] | None:
    graph = {t["id"]: list(t.get("dependencies", [])) for t in tasks}
    WHITE, GREY, BLACK = 0, 1, 2
    color = {k: WHITE for k in graph}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GREY
        stack.append(node)
        for dep in graph.get(node, []):
            if dep not in color:
                continue
            if color[dep] == GREY:
                return stack[stack.index(dep):] + [dep]
            if color[dep] == WHITE:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for node in sorted(graph):
        if color[node] == WHITE:
            found = visit(node)
            if found:
                return found
    return None


def ancestors(tasks: list[dict]) -> dict[str, set[str]]:
    deps = {t["id"]: set(t.get("dependencies", [])) for t in tasks}
    memo: dict[str, set[str]] = {}

    def anc(node: str, seen: frozenset = frozenset()) -> set[str]:
        if node in memo:
            return memo[node]
        out: set[str] = set()
        for d in deps.get(node, ()):
            if d in seen:
                continue
            out.add(d)
            out |= anc(d, seen | {node})
        memo[node] = out
        return out

    return {k: anc(k) for k in deps}


def topo_order(tasks: list[dict]) -> list[str]:
    remaining = {t["id"]: set(t.get("dependencies", [])) for t in tasks}
    order: list[str] = []
    while remaining:
        ready = sorted(k for k, v in remaining.items() if not (v & remaining.keys()))
        if not ready:
            raise ValueError("cycle")
        order += ready
        for k in ready:
            remaining.pop(k)
    return order


def validate_tasks(tasks: list[dict], known_agents: set[str], agent_roles: dict[str, str],
                   requirement_ids: set[str] | None = None, ac_ids: set[str] | None = None) -> dict:
    """Validate a task DAG. Returns {valid, errors, warnings, conflicts, order}.

    conflicts: pairs of *independent* tasks whose owned/shared paths overlap; the
    scheduler must never run such a pair concurrently.
    """
    errors: list[str] = []
    warnings: list[str] = []
    ids = [t.get("id") for t in tasks]
    seen: set[str] = set()
    for tid in ids:
        if tid in seen:
            errors.append(f"duplicate task id {tid}")
        seen.add(tid)
    idset = set(ids)
    for t in tasks:
        tid = t.get("id")
        agent = t.get("agent")
        if agent not in known_agents:
            errors.append(f"{tid}: unknown agent '{agent}'")
        elif agent_roles.get(agent) in ("internal",):
            errors.append(f"{tid}: agent '{agent}' cannot be assigned tasks")
        for dep in t.get("dependencies", []):
            if dep == tid:
                errors.append(f"{tid}: depends on itself")
            elif dep not in idset:
                errors.append(f"{tid}: missing dependency {dep}")
        role = agent_roles.get(agent, "")
        if role in WRITING_ROLES and not t.get("owned_paths"):
            errors.append(f"{tid}: writing task for '{agent}' must declare owned_paths")
        for p in t.get("owned_paths", []):
            n = norm_path(p)
            if n.startswith("/") or ".." in n.split("/") or n.startswith(".devpipeline") or n.startswith(".git") or " " in n.strip():
                errors.append(f"{tid}: illegal owned path '{p}'")
        if requirement_ids is not None:
            for r in t.get("requirements", []):
                if r not in requirement_ids:
                    errors.append(f"{tid}: unknown requirement {r}")
        if ac_ids is not None:
            for a in t.get("acceptance_criteria", []):
                if a not in ac_ids:
                    errors.append(f"{tid}: unknown acceptance criterion {a}")
    cycle = find_cycle([t for t in tasks if t.get("id")])
    if cycle:
        errors.append("dependency cycle: " + " -> ".join(cycle))

    conflicts: list[list[str]] = []
    if not cycle:
        anc = ancestors(tasks)
        for a, b in combinations(tasks, 2):
            if a["id"] in anc.get(b["id"], set()) or b["id"] in anc.get(a["id"], set()):
                continue
            pa = list(a.get("owned_paths", [])) + list(a.get("shared_paths", []))
            pb = list(b.get("owned_paths", [])) + list(b.get("shared_paths", []))
            overlap = [f"{x}~{y}" for x in pa for y in pb if paths_overlap(x, y)]
            if overlap:
                conflicts.append(sorted([a["id"], b["id"]]))
                warnings.append(f"ownership overlap between independent {a['id']} and {b['id']} ({overlap[0]}); they will be serialised")
    order: list[str] = []
    if not errors:
        order = topo_order(tasks)
    return {"valid": not errors, "errors": errors, "warnings": warnings, "conflicts": sorted(conflicts), "order": order}


def ready_tasks(tasks: list[dict], conflicts: list[list[str]], limit: int) -> list[str]:
    """Deterministic wave selection (mirrors workflows/lib/scheduler logic)."""
    status = {t["id"]: t["status"] for t in tasks}
    running = {k for k, v in status.items() if v == "RUNNING"}
    picked: list[str] = []
    for t in sorted(tasks, key=lambda x: x["id"]):
        if t["status"] != "PENDING":
            continue
        if any(status.get(d) != "COMPLETED" for d in t.get("dependencies", [])):
            continue
        clash = any(sorted([t["id"], o]) in conflicts for o in picked + list(running))
        if clash:
            continue
        picked.append(t["id"])
        if len(picked) >= limit:
            break
    return picked


def blocked_by_failure(tasks: list[dict]) -> list[str]:
    status = {t["id"]: t["status"] for t in tasks}
    bad = {"FAILED", "BLOCKED", "CANCELLED"}
    out = []
    changed = True
    while changed:
        changed = False
        for t in tasks:
            if status[t["id"]] == "PENDING" and any(status.get(d) in bad for d in t.get("dependencies", [])):
                status[t["id"]] = "BLOCKED"
                out.append(t["id"])
                changed = True
    return out
