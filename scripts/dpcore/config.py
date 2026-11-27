"""Configuration loading and resolution (defaults <- profile <- project <- CLI)."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from . import CONFIG
from .jsonschema_lite import validate_named
from .util import DPError, read_json

PROFILES = ("fast", "balanced", "strict")
_MERGE_DICT_KEYS = {"model_routing", "effort_routing", "gates", "agents"}


def load(name: str) -> Any:
    return read_json(CONFIG / name)


def registry() -> dict:
    return load("agent-registry.json")


def gates_config() -> dict:
    return load("quality-gates.json")


def stack_registry() -> dict:
    return load("stack-registry.json")


def state_machine() -> dict:
    return load("state-machine.json")


def qa_kinds() -> dict[str, str]:
    """agent -> report kind, from the registry (single source)."""
    return {a: d["report_kind"] for a, d in registry()["agents"].items() if d.get("report_kind")}


def known_agents() -> set[str]:
    return set(registry()["agents"].keys())


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if key in _MERGE_DICT_KEYS and isinstance(value, dict):
            out.setdefault(key, {})
            out[key].update(value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def resolve(profile: str | None = None, project_overrides: dict | None = None,
            cli_overrides: dict | None = None) -> dict:
    """Return the validated, fully resolved configuration.

    model_routing / effort_routing in the result are maps *agent -> value*,
    derived from config/model-routing.json categories unless overridden.
    """
    cfg = load("defaults.json")
    project_overrides = dict(project_overrides or {})
    cli_overrides = dict(cli_overrides or {})
    chosen = cli_overrides.get("profile") or profile or project_overrides.get("profile") or cfg["profile"]
    if chosen not in PROFILES:
        raise DPError(f"unknown profile '{chosen}' (expected one of {', '.join(PROFILES)})")
    cfg = _merge(cfg, load(f"profiles/{chosen}.json"))

    routing = load("model-routing.json")
    cats = dict(routing["default"])
    cats.update(routing["profiles"].get(chosen, {}))
    efforts = dict(routing["effort"]["default"])
    efforts.update(routing["effort"]["profiles"].get(chosen, {}))
    agents = registry()["agents"]
    cfg["model_routing"] = {a: cats.get(d["category"], "sonnet") for a, d in agents.items()} | cfg.get("model_routing", {})
    cfg["effort_routing"] = {a: efforts[d["category"]] for a, d in agents.items() if d["category"] in efforts} | cfg.get("effort_routing", {})

    cfg = _merge(cfg, project_overrides)
    cfg = _merge(cfg, cli_overrides)
    cfg["profile"] = chosen
    errors = validate_config(cfg)
    if errors:
        raise DPError("invalid configuration: " + "; ".join(errors))
    return cfg


def validate_config(cfg: dict) -> list[str]:
    errors = validate_named(cfg, "config.schema.json")
    agents = known_agents()
    for section in ("model_routing", "effort_routing", "agents"):
        for name in cfg.get(section, {}):
            if name not in agents:
                errors.append(f"{section}: unknown agent '{name}'")
    gate_ids = {g["id"] for g in gates_config()["gates"]}
    for gid in cfg.get("gates", {}):
        if gid not in gate_ids:
            errors.append(f"gates: unknown gate '{gid}'")
    reg = registry()["agents"]
    for name, mode in cfg.get("agents", {}).items():
        if mode == "disabled" and reg.get(name, {}).get("activation", {}).get("always"):
            errors.append(f"agents: '{name}' is always required and cannot be disabled")
    return errors


def project_config_path(project: Path) -> Path:
    return project / ".devpipeline" / "config.json"
