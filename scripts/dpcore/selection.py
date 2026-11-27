"""Registry-driven dynamic agent selection and repository feature detection."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config as cfgmod

FEATURE_KEYS = ["frontend", "backend", "database", "ai", "rag", "mobile", "devops", "browser_ui",
                "performance_requirements", "documentation", "authentication", "payments", "external_messaging"]


def merge_features(*sources: dict | None) -> dict:
    out = {k: False for k in FEATURE_KEYS}
    for src in sources:
        for k, v in (src or {}).items():
            if k in out and v:
                out[k] = True
    if out["rag"]:
        out["ai"] = True
    if out["frontend"]:
        out["browser_ui"] = True
    return out


def _rule_hits(activation: dict, features: dict, profile: str, impl_count: int, mode: str) -> list[str]:
    hits = []
    if activation.get("always"):
        hits.append("always required")
    for f in activation.get("any_feature", []):
        if features.get(f):
            hits.append(f"feature '{f}'")
    if profile in activation.get("profiles", []):
        hits.append(f"profile '{profile}'")
    need = activation.get("min_implementation_agents")
    if need is not None and impl_count >= need:
        hits.append(f"{impl_count} implementation agent(s) active")
    return hits


def select_agents(features: dict, profile: str, mode: str, architect_agents: list[str] | None = None,
                  task_agents: list[str] | None = None, overrides: dict | None = None) -> dict:
    """Return {"selected": [{agent, reason, mandatory, source}], "errors": [...], "decisions": [...]}.

    Locked agents activated by a rule cannot be removed by the architect or by
    config, so e.g. an AI project always gets the AI Evaluation Engineer.
    """
    reg = cfgmod.registry()
    agents = reg["agents"]
    impl_roles = set(reg["implementation_roles"])
    overrides = overrides or {}
    errors: list[str] = []
    decisions: list[dict] = []
    chosen: dict[str, dict] = {}

    def add(name: str, reason: str, mandatory: bool, source: str) -> None:
        if name in chosen:
            chosen[name]["reason"] += f"; {reason}"
            chosen[name]["mandatory"] = chosen[name]["mandatory"] or mandatory
        else:
            chosen[name] = {"agent": name, "reason": reason, "mandatory": mandatory, "source": source}

    audit = mode == "audit"
    # pass 1: feature/profile rules for non-dependent agents
    for name, d in agents.items():
        act = d.get("activation", {})
        if act.get("internal") or act.get("on_demand"):
            continue
        if audit and d["role"] in ("implementation", "planning", "integration", "documentation"):
            continue
        hits = _rule_hits({k: v for k, v in act.items() if k != "min_implementation_agents"}, features, profile, 0, mode)
        if hits:
            add(name, "rule: " + ", ".join(hits), bool(d.get("lock")), "rule")

    for name in architect_agents or []:
        if name not in agents or agents[name]["activation"].get("internal"):
            errors.append(f"architect requested unknown agent '{name}'")
            continue
        if not audit:
            add(name, "architect execution plan", False, "architect")
    for name in task_agents or []:
        if name not in agents:
            errors.append(f"task assigned to unknown agent '{name}'")
            continue
        add(name, "has assigned tasks", True, "tasks")

    for name, mode_ in overrides.items():
        if name not in agents:
            errors.append(f"config override for unknown agent '{name}'")
        elif mode_ == "required":
            add(name, "config: required", True, "config")

    for name, d in agents.items():  # e.g. ai-engineer active (from tasks) => ai-evaluation-engineer, locked
        req = d.get("activation", {}).get("requires_agent")
        if req and req in chosen and name not in chosen:
            add(name, f"rule: '{req}' is active", bool(d.get("lock")), "rule")

    impl_count = sum(1 for n in chosen if agents[n]["role"] in impl_roles)
    for name, d in agents.items():
        need = d.get("activation", {}).get("min_implementation_agents")
        if need is not None and impl_count >= need and not audit:
            add(name, f"rule: {impl_count} implementation agent(s) active", bool(d.get("lock")), "rule")
    if audit:
        add("code-qa-engineer", "audit mode: code inspection", True, "rule")

    for name, mode_ in overrides.items():
        if mode_ != "disabled" or name not in chosen:
            continue
        entry = chosen[name]
        if entry["mandatory"] and entry["source"] in ("rule", "tasks"):
            errors.append(f"cannot disable '{name}': {entry['reason']}")
            decisions.append({"agent": name, "decision": "kept", "why": "locked by rule"})
        else:
            chosen.pop(name)
            decisions.append({"agent": name, "decision": "disabled", "why": "config override"})

    for name in agents:
        if name in chosen:
            decisions.append({"agent": name, "decision": "activated", "why": chosen[name]["reason"]})
        elif not agents[name]["activation"].get("internal"):
            decisions.append({"agent": name, "decision": "not activated", "why": "no activation rule matched"})
    ordered = [chosen[n] for n in agents if n in chosen]
    return {"selected": ordered, "errors": errors, "decisions": decisions}


# ---------------------------------------------------------------------------
# Feature detection for existing repositories (audit / improve baseline)
# ---------------------------------------------------------------------------
_FRONT = re.compile(r'"(react|next|vue|nuxt|svelte|@sveltejs/kit|@angular/core|solid-js|astro|vite)"')
_BACK_JS = re.compile(r'"(express|fastify|@nestjs/core|koa|hono)"')
_DB_JS = re.compile(r'"(prisma|@prisma/client|drizzle-orm|pg|mysql2|mongoose|mongodb|sqlite3|better-sqlite3|typeorm|sequelize)"')
_AI_JS = re.compile(r'"(openai|@anthropic-ai/sdk|ai|langchain|@langchain/[a-z-]+|@google/generative-ai|ollama)"')
_PY_BACK = re.compile(r"\b(fastapi|django|flask|starlette|litestar)\b", re.I)
_PY_DB = re.compile(r"\b(sqlalchemy|alembic|psycopg|psycopg2|asyncpg|pymongo|sqlmodel|peewee)\b", re.I)
_PY_AI = re.compile(r"\b(openai|anthropic|langchain|langgraph|llama[-_]index|google-generativeai|ollama|transformers)\b", re.I)
_PY_RAG = re.compile(r"\b(pgvector|chromadb|faiss|qdrant|weaviate|pinecone|llama[-_]index)\b", re.I)
_MOBILE = re.compile(r'"(react-native|expo)"')


def detect_features(project: Path, max_depth: int = 2) -> dict:
    feats = {k: False for k in FEATURE_KEYS}
    ignore = set(cfgmod.stack_registry()["ignore_dirs"])

    def walk(d: Path, depth: int):
        yield d
        if depth >= max_depth:
            return
        try:
            for child in sorted(d.iterdir()):
                if child.is_dir() and child.name not in ignore and not child.name.startswith("."):
                    yield from walk(child, depth + 1)
        except OSError:
            return

    for d in walk(project, 0):
        pkg = d / "package.json"
        if pkg.exists():
            text = pkg.read_text(errors="ignore")
            feats["frontend"] |= bool(_FRONT.search(text))
            feats["backend"] |= bool(_BACK_JS.search(text))
            feats["database"] |= bool(_DB_JS.search(text))
            feats["ai"] |= bool(_AI_JS.search(text))
            feats["mobile"] |= bool(_MOBILE.search(text))
        for name in ("pyproject.toml", "requirements.txt"):
            f = d / name
            if f.exists():
                text = f.read_text(errors="ignore")
                feats["backend"] |= bool(_PY_BACK.search(text))
                feats["database"] |= bool(_PY_DB.search(text))
                feats["ai"] |= bool(_PY_AI.search(text))
                feats["rag"] |= bool(_PY_RAG.search(text))
        if (d / "pubspec.yaml").exists():
            feats["mobile"] = True
        if (d / "Dockerfile").exists() or (d / "docker-compose.yml").exists() or (d / "compose.yaml").exists():
            feats["devops"] = True
        if (d / "pom.xml").exists() or list(d.glob("*.csproj")) or (d / "go.mod").exists():
            feats["backend"] = True
    return merge_features(feats)


def dump(obj) -> str:
    return json.dumps(obj, indent=2)
