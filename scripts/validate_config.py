#!/usr/bin/env python3
"""Validate every DevPipeline configuration artifact (framework and, optionally, a project).

  validate_config.py [--project DIR] [--json]

Checks: JSON syntax of all config/schema/manifest files; schema keyword support;
profile resolution against config.schema.json; agent registry <-> agents/*.md
consistency (frontmatter names, models, tools); gate ids/sources; state machine
closure; model aliases; hooks.json structure; plugin.json / marketplace.json /
.mcp.json shape; skills have SKILL.md with frontmatter; workflows start with a
pure-literal `export const meta`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dpcore import ROOT  # noqa: E402
from dpcore import config as cfgmod  # noqa: E402
from dpcore.jsonschema_lite import load_schema, unsupported_keywords, validate_named  # noqa: E402

AGENT_FIELDS = {"name", "description", "tools", "disallowedTools", "model", "permissionMode", "maxTurns", "skills",
                "mcpServers", "hooks", "memory", "background", "effort", "isolation", "color", "initialPrompt",
                "experimental", "omitClaudeMd", "autoCompactWindow"}
PLUGIN_IGNORED = {"hooks", "mcpServers", "permissionMode", "initialPrompt"}
SKILL_FIELDS = {"name", "description", "when_to_use", "argument-hint", "arguments", "disable-model-invocation",
                "user-invocable", "allowed-tools", "disallowed-tools", "model", "effort", "context", "agent", "background",
                "hooks", "paths", "shell", "metadata", "license", "compatibility"}
MODELS = {"sonnet", "opus", "haiku", "fable", "inherit"}
COMMAND_SKILLS = {"build-project", "improve-project", "audit-project", "repair-project", "project-status", "resume-project", "project-doctor"}


def frontmatter(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    out: dict = {}
    key = None
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
        if m and not line.startswith(" "):
            key, val = m.group(1), m.group(2).strip()
            out[key] = val.strip('"').strip("'") if val else []
        elif key and line.strip().startswith("- "):
            if not isinstance(out[key], list):
                out[key] = []
            out[key].append(line.strip()[2:].strip())
    return out


def run(project: Path | None = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    for f in sorted(list((ROOT / "config").rglob("*.json")) + list((ROOT / "schemas").glob("*.json"))
                    + [ROOT / ".claude-plugin" / "plugin.json", ROOT / ".claude-plugin" / "marketplace.json", ROOT / ".mcp.json",
                       ROOT / "hooks" / "hooks.json"]):
        try:
            json.loads(f.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{f.relative_to(ROOT)}: {exc}")
    for f in sorted((ROOT / "schemas").glob("*.json")):
        bad = unsupported_keywords(load_schema(f.name))
        if bad:
            errors.append(f"{f.name}: unsupported schema keywords {sorted(bad)}")
    for p in cfgmod.PROFILES:
        try:
            cfgmod.resolve(p)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"profile {p}: {exc}")
    reg = cfgmod.registry()
    for name, d in reg["agents"].items():
        md = ROOT / "agents" / f"{name}.md"
        if not md.exists():
            errors.append(f"agent-registry: {name} has no agents/{name}.md")
            continue
        fm = frontmatter(md)
        if fm is None:
            errors.append(f"agents/{name}.md: missing YAML frontmatter")
            continue
        if fm.get("name") != name:
            errors.append(f"agents/{name}.md: frontmatter name '{fm.get('name')}' != '{name}'")
        if not fm.get("description"):
            errors.append(f"agents/{name}.md: description required")
        unknown = set(fm) - AGENT_FIELDS
        if unknown:
            errors.append(f"agents/{name}.md: unsupported frontmatter fields {sorted(unknown)}")
        ignored = set(fm) & PLUGIN_IGNORED
        if ignored:
            warnings.append(f"agents/{name}.md: {sorted(ignored)} are ignored for plugin agents")
        if fm.get("model") and fm["model"] not in MODELS:
            errors.append(f"agents/{name}.md: unknown model alias {fm['model']}")
        body = md.read_text().split("\n---", 2)[-1]
        for section in ("Role", "Responsibilities", "Inputs", "Outputs", "File ownership", "Prohibited", "Standards",
                        "Validation", "Communication", "Definition of done", "Failure", "Security"):
            if f"## {section}" not in body and d["role"] != "internal":
                errors.append(f"agents/{name}.md: missing section '## {section}'")
    for md in sorted((ROOT / "agents").glob("*.md")):
        if md.stem not in reg["agents"]:
            errors.append(f"agents/{md.name}: not registered in config/agent-registry.json")
    gates = cfgmod.gates_config()["gates"]
    ids = [g["id"] for g in gates]
    if len(ids) != len(set(ids)):
        errors.append("quality-gates: duplicate gate ids")
    for g in gates:
        if g["source"] not in ("plan_validation", "tasks", "coverage", "checks", "report", "review", "documentation", "issues"):
            errors.append(f"gate {g['id']}: unknown source {g['source']}")
        if g["default"] not in ("mandatory", "optional", "disabled"):
            errors.append(f"gate {g['id']}: bad default")
    sm = cfgmod.state_machine()
    states = set(load_schema("state.schema.json")["$defs"]["run"]["properties"]["phase"]["enum"])
    for frm, tos in sm["transitions"].items():
        if frm not in states:
            errors.append(f"state-machine: unknown state {frm}")
        for to in tos:
            if to not in states:
                errors.append(f"state-machine: {frm} -> unknown {to}")
    if states - set(sm["transitions"]):
        errors.append(f"state-machine: states without transition entry {sorted(states - set(sm['transitions']))}")
    routing = cfgmod.load("model-routing.json")
    for scope in [routing["default"]] + list(routing["profiles"].values()):
        for cat, model in scope.items():
            if model not in MODELS:
                errors.append(f"model-routing: {cat} -> unknown alias {model}")
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    if "hooks" not in hooks:
        errors.append("hooks/hooks.json: missing top-level 'hooks' wrapper")
    for event, groups in hooks.get("hooks", {}).items():
        for grp in groups:
            for h in grp.get("hooks", []):
                if h.get("type") != "command":
                    errors.append(f"hooks: {event} handler type must be command")
                for arg in h.get("args", []):
                    if "scripts/" in arg and not (ROOT / arg.replace("${CLAUDE_PLUGIN_ROOT}/", "")).exists():
                        errors.append(f"hooks: {event} references missing {arg}")
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    if plugin.get("name") != "devpipeline":
        errors.append("plugin.json: name must be 'devpipeline'")
    if plugin.get("version") != (ROOT / "VERSION").read_text().strip():
        errors.append("plugin.json version != VERSION")
    mcp = json.loads((ROOT / ".mcp.json").read_text())
    for name, srv in mcp.get("mcpServers", {}).items():
        blob = json.dumps(srv)
        if re.search(r"(?i)(bearer\s+[a-z0-9]|api[_-]?key\"\s*:\s*\"[^$])", blob):
            errors.append(f".mcp.json: {name} appears to hardcode credentials")
        if "@latest" in blob:
            warnings.append(f".mcp.json: {name} uses @latest (unpinned)")
    for sk in sorted((ROOT / "skills").iterdir()):
        if not sk.is_dir():
            continue
        f = sk / "SKILL.md"
        if not f.exists():
            errors.append(f"skills/{sk.name}: SKILL.md missing")
            continue
        fm = frontmatter(f)
        if not fm or not fm.get("description"):
            errors.append(f"skills/{sk.name}: frontmatter with description required")
            continue
        unknown = set(fm) - SKILL_FIELDS
        if unknown:
            errors.append(f"skills/{sk.name}: unsupported frontmatter fields {sorted(unknown)}")
        lines = len(f.read_text().splitlines())
        command_skill = sk.name in COMMAND_SKILLS
        words = len(f.read_text().split())
        minimum = 250 if command_skill else 450
        if words < minimum:
            errors.append(f"skills/{sk.name}: SKILL.md too thin ({words} words, need >= {minimum})")
        body = f.read_text()
        if not command_skill:
            for section in ("## Activation criteria", "## Required inputs", "## References"):
                if section not in body:
                    errors.append(f"skills/{sk.name}: missing section '{section}'")
            if "Completion criteria" not in body:
                errors.append(f"skills/{sk.name}: missing completion criteria")
        elif "${CLAUDE_PLUGIN_ROOT}/scripts/" not in body:
            errors.append(f"skills/{sk.name}: command skill must invoke the DevPipeline helpers/workflow")
        if lines > 500:
            warnings.append(f"skills/{sk.name}: SKILL.md over 500 lines; move detail to reference files")
    meta_re = re.compile(r"^export const meta = \{\n(?:  [a-zA-Z]+: (?:'[^'\n]*'|\[[^\n]*\]),?\n)+\}", re.M)
    for wf in sorted((ROOT / "workflows").glob("*.js")):
        text = wf.read_text()
        if not text.startswith("export const meta = {"):
            errors.append(f"workflows/{wf.name}: must start with 'export const meta = {{'")
        elif not meta_re.match(text):
            errors.append(f"workflows/{wf.name}: meta must be a pure literal (strings/arrays of literals)")
        if re.search(r"\bimport\s*\(|^\s*import\s", text, re.M):
            errors.append(f"workflows/{wf.name}: imports are not allowed in workflow scripts")
        code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("//"))
        if re.search(r"Date\.now\(\)|Math\.random\(\)|new Date\(\)", code):
            errors.append(f"workflows/{wf.name}: Date.now/Math.random/new Date() throw in the workflow runtime")
        if re.search(r"\brequire\(|\bprocess\.|\bfs\.", code):
            errors.append(f"workflows/{wf.name}: Node APIs (require/process/fs) are unavailable in the workflow runtime")
    if project:
        pc = Path(project) / ".devpipeline" / "config.json"
        if pc.exists():
            errs = validate_named(json.loads(pc.read_text()), "project.schema.json")
            errors += [f"project config: {e}" for e in errs]
            try:
                pcfg = json.loads(pc.read_text())
                cfgmod.resolve(pcfg.get("profile"), pcfg.get("overrides"))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"project overrides: {exc}")
        else:
            warnings.append(f"{pc} not found (project not initialised)")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = run(Path(a.project) if a.project else None)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        for e in res["errors"]:
            print(f"ERROR   {e}")
        for w in res["warnings"]:
            print(f"WARNING {w}")
        print("configuration valid" if res["ok"] else f"configuration INVALID ({len(res['errors'])} error(s))")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
