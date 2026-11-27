#!/usr/bin/env python3
"""DevPipeline environment diagnostics.

  doctor.py [--project DIR] [--json] [--no-claude] [--mcp]

Statuses: OK, WARNING, MISSING, UNSUPPORTED, ERROR. Never prints secrets:
settings files are inspected only for the specific keys listed below.
--no-claude skips calls to the `claude` CLI (used by tests);
--mcp additionally health-checks MCP servers through `claude mcp list` (slow: it starts them).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dpcore import ROOT, framework_version  # noqa: E402
import validate_config  # noqa: E402

MIN_CLAUDE = (2, 1, 269)
CLAUDE = os.environ.get("CLAUDE_BIN", "claude")
EXPECTED_AGENTS = sorted(p.stem for p in (ROOT / "agents").glob("*.md"))
EXPECTED_SKILLS = sorted(p.name for p in (ROOT / "skills").iterdir() if (p / "SKILL.md").exists())
EXPECTED_WORKFLOWS = sorted(p.stem for p in (ROOT / "workflows").glob("*.js"))


def sh(*cmd: str, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def version_tuple(text: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(x) for x in m.groups()) if m else (0,)


def checks(project: Path | None, use_claude: bool, mcp: bool) -> list[dict]:
    out: list[dict] = []

    def add(name: str, status: str, detail: str) -> None:
        out.append({"check": name, "status": status, "detail": detail})

    add("framework", "OK", f"DevPipeline {framework_version()} at {ROOT}")
    add("platform", "OK", f"{sys.platform} / python {sys.version.split()[0]}")
    if sys.version_info < (3, 9):
        add("python", "UNSUPPORTED", "python >= 3.9 required")
    # executables
    req = {"python3": True, "git": True, "node": True, "npx": True}
    opt = ["npm", "pnpm", "uv", "docker", "ruff", "pytest", "semgrep", "gitleaks", "pip-audit", "trivy", "osv-scanner", "k6"]
    for exe, _ in req.items():
        path = shutil.which(exe)
        if not path:
            add(f"exe:{exe}", "MISSING", "required")
            continue
        code, ver = sh(exe, "--version")
        status = "OK"
        if exe == "node" and version_tuple(ver) < (20, 0, 0):
            status = "WARNING"
        add(f"exe:{exe}", status, ver.strip().splitlines()[0] if ver.strip() else path)
    for exe in opt:
        add(f"exe:{exe}", "OK" if shutil.which(exe) else "WARNING",
            shutil.which(exe) or "not installed (optional; dependent checks report NOT_RUN, never PASS)")
    # configuration
    res = validate_config.run(project)
    add("config", "OK" if res["ok"] else "ERROR", "valid" if res["ok"] else "; ".join(res["errors"][:5]))
    for w in res["warnings"][:5]:
        add("config:warning", "WARNING", w)
    add("schemas", "OK" if len(list((ROOT / "schemas").glob("*.schema.json"))) >= 10 else "MISSING",
        f"{len(list((ROOT / 'schemas').glob('*.schema.json')))} schema files")
    # hook guard fast path
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "hook_guard.py"), "pre"], input='{"tool_name":"Bash"}',
                       capture_output=True, text=True, timeout=20)
    add("hooks", "OK" if p.returncode == 0 and not p.stdout.strip() else "ERROR",
        "hook_guard fast path exits 0 for non-DevPipeline calls" if p.returncode == 0 else p.stderr[:200])
    # workflows availability (settings keys only)
    disabled = os.environ.get("CLAUDE_CODE_DISABLE_WORKFLOWS") == "1"
    enabled = None
    for sp in [Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "settings.json"] + \
              ([project / ".claude" / "settings.json", project / ".claude" / "settings.local.json"] if project else []):
        try:
            data = json.loads(sp.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("disableWorkflows") is True:
            disabled = True
        if "enableWorkflows" in data:
            enabled = data["enableWorkflows"]
    if use_claude:
        code, ver = sh(CLAUDE, "--version")
        if code != 0:
            add("claude", "MISSING", "claude CLI not found")
        else:
            v = version_tuple(ver)
            add("claude", "OK" if v >= MIN_CLAUDE else "UNSUPPORTED", ver.strip() + ("" if v >= MIN_CLAUDE else f" (need >= {'.'.join(map(str, MIN_CLAUDE))})"))
            if disabled or enabled is False or v < (2, 1, 248):
                add("dynamic-workflows", "UNSUPPORTED", "disabled (disableWorkflows / enableWorkflows=false / env / version) - "
                    "fallback mode only: docs/WORKFLOWS.md#fallback-mode")
            elif enabled is True:
                add("dynamic-workflows", "OK", "enableWorkflows=true in settings")
            else:
                add("dynamic-workflows", "WARNING", "enableWorkflows is unset (default depends on plan). If the Workflow tool is missing, "
                    "enable 'Dynamic workflows' in /config or run scripts/install.sh --enable-workflows; verify with tests/smoke/run_runtime_smoke.sh")
        code, txt = sh(CLAUDE, "plugin", "list", "--json")
        installed = []
        try:
            data = json.loads(txt)
            items = data if isinstance(data, list) else data.get("plugins", data)
            installed = [json.dumps(i) for i in (items if isinstance(items, list) else [items])]
        except json.JSONDecodeError:
            installed = [txt]
        blob = "\n".join(installed)
        dp = "devpipeline@devpipeline-local" in blob or '"devpipeline"' in blob
        add("plugin:devpipeline", "OK" if dp else "MISSING",
            "installed" if dp else "not installed — run scripts/install.sh (or use --plugin-dir for a session)")
        ux = "ui-ux-pro-max" in blob
        add("skill:ui-ux-pro-max", "OK" if ux else "MISSING",
            "installed via official marketplace" if ux else "required for UI work: scripts/install.sh --with-ui-ux-pro-max")
        if dp:
            code, det = sh(CLAUDE, "plugin", "details", "devpipeline")
            miss_a = [a for a in EXPECTED_AGENTS if a not in det]
            miss_s = [s for s in EXPECTED_SKILLS if s not in det]
            install_path = next((json.loads(i).get("installPath") for i in installed
                                 if i.startswith("{") and '"devpipeline@devpipeline-local"' in i), None)
            wf_out = det
            if install_path:
                _, wf_out = sh(CLAUDE, "plugin", "validate", install_path, timeout=120)  # lists "workflows: devpipeline:pipeline-*"
            miss_w = [w for w in ("pipeline-build", "pipeline-improve", "pipeline-audit", "pipeline-repair") if f"devpipeline:{w}" not in wf_out]
            add("discovery:agents", "OK" if not miss_a else "ERROR", f"{len(EXPECTED_AGENTS) - len(miss_a)}/{len(EXPECTED_AGENTS)} agents listed" + (f"; missing {miss_a}" if miss_a else ""))
            add("discovery:skills", "OK" if not miss_s else "ERROR", f"{len(EXPECTED_SKILLS) - len(miss_s)}/{len(EXPECTED_SKILLS)} skills listed" + (f"; missing {miss_s}" if miss_s else ""))
            add("discovery:workflows", "OK" if not miss_w else "WARNING",
                "4 workflows discovered by the runtime in the installed copy (devpipeline:pipeline-*)" if not miss_w else f"not discovered: {miss_w} (check /workflows in a session)")
        if mcp:
            code, txt = sh(CLAUDE, "mcp", "list", timeout=180)
            for srv in ("playwright", "context7"):
                line = next((l for l in txt.splitlines() if srv in l), "")
                status = "OK" if ("✓" in line or "Connected" in line) else ("MISSING" if not line else "WARNING")
                add(f"mcp:{srv}", status, line.strip() or "not listed")
        else:
            add("mcp", "WARNING", "not health-checked (run doctor.py --mcp; it starts the servers)")
    else:
        add("claude", "WARNING", "skipped (--no-claude)")
    if project:
        try:
            project.mkdir(parents=True, exist_ok=True)
            probe = project / ".devpipeline-doctor-probe"
            probe.write_text("ok")
            probe.unlink()
            add("project:writable", "OK", str(project))
        except OSError as exc:
            add("project:writable", "ERROR", str(exc))
        code, _ = sh("git", "-C", str(project), "rev-parse", "--is-inside-work-tree")
        add("project:git", "OK" if code == 0 else "WARNING",
            "git repository (worktree isolation available)" if code == 0 else "not a git repo: new projects get `git init`; existing ones run serialised")
    add("limitation:budget", "WARNING", "monetary/token budgets are not hard limits; agent-call, operator-call and repair limits are enforced deterministically; budget.spent() counts output tokens only")
    add("limitation:hooks", "WARNING", "plugin agents ignore permissionMode/mcpServers/hooks frontmatter; enforcement uses plugin-level hooks keyed on agent_type")
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-claude", action="store_true")
    ap.add_argument("--mcp", action="store_true")
    a = ap.parse_args(argv)
    res = checks(Path(a.project).resolve() if a.project else None, not a.no_claude, a.mcp)
    bad = [r for r in res if r["status"] in ("ERROR", "MISSING", "UNSUPPORTED") and not r["check"].startswith("exe:") or
           (r["check"] in ("exe:python3", "exe:git", "exe:node", "exe:npx") and r["status"] != "OK")]
    if a.json:
        print(json.dumps({"ok": not bad, "checks": res}, indent=2))
    else:
        for r in res:
            print(f"{r['status']:<12} {r['check']:<24} {r['detail']}")
        print("\nDevPipeline doctor: " + ("healthy" if not bad else f"{len(bad)} problem(s)"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
