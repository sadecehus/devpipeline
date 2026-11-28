#!/usr/bin/env python3
"""Stack-aware, evidence-producing validation runner.

Discovers validation commands from real manifests (package.json scripts,
pyproject/uv.lock, pom.xml, go.mod, *.csproj ...) plus architect-declared
checks, executes them, and writes a `test-report` (kind=checks) whose every
check records: command, cwd, exit code, start/end, duration, content
fingerprint, parsed test counts and a redacted log file.

Commands are never invented: a script/tool that does not exist yields
NOT_RUN with a reason (never PASS).

  run_checks.py --project DIR --run ID --label LABEL [--dir PATH] [--only kind,...] [--baseline]
  run_checks.py --discover --dir PATH          # print discovered checks only
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dpcore import config as cfgmod  # noqa: E402
from dpcore import gitutil  # noqa: E402
from dpcore.util import DPError, atomic_write_json, emit, now_iso, read_json  # noqa: E402
from redact_secrets import redact  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
SECRET_ENV = re.compile(r"(?i)(API_?KEY|TOKEN|SECRET|PASSWORD|PRIVATE|CREDENTIAL|^AWS_|^AZURE_|^GOOGLE_APPLICATION)")
SHELL_META = re.compile(r"[;&|<>`$\\]|\$\(")


# ------------------------------------------------------------ discovery ----
def _dirs(root: Path, depth: int, ignore: set[str]):
    yield root
    if depth <= 0:
        return
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name not in ignore and not child.name.startswith("."):
            yield from _dirs(child, depth - 1, ignore)


def _rel(d: Path, root: Path) -> str:
    r = d.relative_to(root).as_posix()
    return "." if r == "." else r


def discover(root: Path, architecture: dict | None = None) -> list[dict]:
    reg = cfgmod.stack_registry()
    ignore = set(reg["ignore_dirs"])
    checks: list[dict] = []
    for d in _dirs(root, reg["search_depth"], ignore):
        rel = _rel(d, root)
        tag = rel.replace("/", ":") if rel != "." else "root"
        pkg = d / "package.json"
        if pkg.exists():
            node = reg["stacks"]["node"]
            try:
                scripts = json.loads(pkg.read_text()).get("scripts", {}) or {}
                deps_text = pkg.read_text()
            except json.JSONDecodeError:
                checks.append({"name": f"node:{tag}:manifest", "kind": "build", "cwd": rel, "command": None,
                               "not_run_reason": "package.json is not valid JSON", "force_status": "FAIL"})
                continue
            pm, install = None, None
            for entry in node["package_managers"]:
                if (d / entry["lockfile"]).exists():
                    pm, install = entry["pm"], entry["install"]
                    break
            if install:
                checks.append({"name": f"node:{tag}:install", "kind": "install", "cwd": rel, "command": install})
            else:
                checks.append({"name": f"node:{tag}:install", "kind": "install", "cwd": rel, "command": None,
                               "not_run_reason": "no lockfile: reproducible install impossible", "force_status": "FAIL"})
                pm = "npm"
            used = set()
            for kind, names in node["scripts"].items():
                for s in names:
                    if s in scripts and s not in used:
                        used.add(s)
                        checks.append({"name": f"node:{tag}:{kind}", "kind": kind, "cwd": rel,
                                       "command": node["run"].format(pm=pm, script=s)})
                        break
            if not any(c["kind"] == "typecheck" and c["cwd"] == rel for c in checks) and (d / "tsconfig.json").exists() \
                    and '"typescript"' in deps_text:
                checks.append({"name": f"node:{tag}:typecheck", "kind": "typecheck", "cwd": rel, "command": node["tsc_fallback"]})
            if pm in node["dependency_audit"] and install:
                checks.append({"name": f"dependency-audit:node:{tag}", "kind": "security", "cwd": rel,
                               "command": node["dependency_audit"][pm], "network": True})
        if any((d / f).exists() for f in reg["stacks"]["python"]["detect"]):
            checks += _python_checks(d, rel, tag, reg["stacks"]["python"])
        for key in ("maven", "gradle", "go"):
            st = reg["stacks"][key]
            if any((d / f).exists() for f in st["detect"]):
                for kind, cmd in st["commands"].items():
                    exe = shlex.split(cmd)[0]
                    if exe.startswith("./") and not (d / exe[2:]).exists():
                        checks.append({"name": f"{key}:{tag}:{kind}", "kind": kind, "cwd": rel, "command": None, "not_run_reason": f"{exe} missing"})
                    else:
                        checks.append({"name": f"{key}:{tag}:{kind}", "kind": kind, "cwd": rel, "command": cmd})
        st = reg["stacks"]["dotnet"]
        if any(list(d.glob(g)) for g in st["detect_glob"]):
            for kind, cmd in st["commands"].items():
                checks.append({"name": f"dotnet:{tag}:{kind}", "kind": kind, "cwd": rel, "command": cmd})
    def same(a, b):
        return (a.get("command") or "").split() == (b.get("command") or "").split() and \
            str(Path(a.get("cwd") or ".")) == str(Path(b.get("cwd") or "."))

    for c in (architecture or {}).get("checks", []):
        decl = {"command": c["command"], "cwd": c.get("cwd", ".")}
        checks = [x for x in checks if x["name"] != c["name"] and not same(x, decl)]
        checks.append({"name": c["name"], "kind": c["kind"], "cwd": c.get("cwd", "."), "command": c["command"],
                       "timeout": c.get("timeout_seconds"), "declared": True})
    checks.append({"name": "secret-scan", "kind": "security", "cwd": ".", "builtin": "secret-scan",
                   "command": f"python3 {shlex.quote(str(SCRIPT_DIR / 'redact_secrets.py'))} scan ."})
    return checks


def _python_checks(d: Path, rel: str, tag: str, st: dict) -> list[dict]:
    out = []
    pyproject = (d / "pyproject.toml").read_text(errors="ignore") if (d / "pyproject.toml").exists() else ""
    reqs = (d / "requirements.txt").read_text(errors="ignore") if (d / "requirements.txt").exists() else ""
    deps = pyproject + "\n" + reqs
    uv_project = (d / "uv.lock").exists() and shutil.which("uv")
    for entry in st["install"]:
        if (d / entry["needs_file"]).exists():
            out.append({"name": f"python:{tag}:install", "kind": "install", "cwd": rel,
                        "command": entry["cmd"] if shutil.which("uv") else None,
                        **({} if shutil.which("uv") else {"not_run_reason": "uv not installed"})})
    for kind, options in st["tools"].items():
        chosen = None
        for opt in options:
            if "needs" in opt and shutil.which(opt["needs"]):
                chosen = opt["cmd"]
            elif "needs_uv_dep" in opt and uv_project and re.search(rf"\b{opt['needs_uv_dep']}\b", deps):
                chosen = opt["cmd"]
            elif "needs_module" in opt and importlib.util.find_spec(opt["needs_module"]):
                chosen = opt["cmd"]
            elif "needs_config" in opt and (f"[tool.{opt['needs_config']}]" in pyproject) and shutil.which(opt["needs_config"]):
                chosen = opt["cmd"]
            if chosen:
                break
        has_tests = any(d.glob("test*/**/*.py")) or any(d.glob("**/test_*.py"))
        if chosen:
            out.append({"name": f"python:{tag}:{kind}", "kind": kind, "cwd": rel, "command": chosen})
        elif kind == "unit" and has_tests:
            out.append({"name": f"python:{tag}:unit", "kind": "unit", "cwd": rel, "command": None,
                        "not_run_reason": "tests exist but pytest is unavailable (add it to the uv project or install it)"})
        elif kind == "lint" and re.search(r"\bruff\b", deps):
            out.append({"name": f"python:{tag}:lint", "kind": "lint", "cwd": rel, "command": None,
                        "not_run_reason": "ruff declared but not runnable (no uv.lock and no ruff on PATH)"})
    audit = st["dependency_audit"][0]
    out.append({"name": f"dependency-audit:python:{tag}", "kind": "security", "cwd": rel,
                "command": audit["cmd"] if shutil.which(audit["needs"]) else None, "network": True,
                **({} if shutil.which(audit["needs"]) else {"not_run_reason": "pip-audit not installed (scanner unavailable is NOT a pass)"})})
    return out


# ------------------------------------------------------------ execution ----
def parse_counts(text: str) -> dict:
    tail = text[-20000:]
    res = {"tests_total": None, "tests_passed": None, "tests_failed": None, "tests_skipped": None}

    def last_int(pattern: str):
        found = re.findall(pattern, tail, flags=re.I | re.M)
        return int(found[-1]) if found else None

    m = re.findall(r"Tests:\s+(.*?)(\d+) total", tail)  # jest
    if m:
        seg = m[-1][0]
        res["tests_total"] = int(m[-1][1])
        res["tests_failed"] = int((re.search(r"(\d+) failed", seg) or [0, 0])[1])
        res["tests_skipped"] = int((re.search(r"(\d+) (?:skipped|todo)", seg) or [0, 0])[1])
        res["tests_passed"] = int((re.search(r"(\d+) passed", seg) or [0, 0])[1])
        return res
    m = re.findall(r"^\s*Tests\s+(.*)\((\d+)\)\s*$", tail, flags=re.M)  # vitest
    if m:
        seg = m[-1][0]
        res["tests_total"] = int(m[-1][1])
        res["tests_failed"] = int((re.search(r"(\d+) failed", seg) or [0, 0])[1])
        res["tests_skipped"] = int((re.search(r"(\d+) (?:skipped|todo)", seg) or [0, 0])[1])
        res["tests_passed"] = int((re.search(r"(\d+) passed", seg) or [0, 0])[1])
        return res
    m = re.search(r"Failed:\s*(\d+),\s*Passed:\s*(\d+),\s*Skipped:\s*(\d+),\s*Total:\s*(\d+)", tail)  # dotnet
    if m:
        f, p, s, t = map(int, m.groups())
        return {"tests_total": t, "tests_passed": p, "tests_failed": f, "tests_skipped": s}
    m = re.findall(r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)", tail)  # maven
    if m:
        t, f, e, s = map(int, m[-1])
        return {"tests_total": t, "tests_passed": t - f - e - s, "tests_failed": f + e, "tests_skipped": s}
    tap = {k: last_int(rf"^(?:#|\u2139)\s*{k} (\d+)\s*$") for k in ("tests", "pass", "fail", "skipped", "todo", "cancelled")}
    if tap["tests"] is not None:  # node:test (TAP or spec reporter)
        return {"tests_total": tap["tests"], "tests_passed": tap["pass"] or 0,
                "tests_failed": (tap["fail"] or 0) + (tap["cancelled"] or 0), "tests_skipped": (tap["skipped"] or 0) + (tap["todo"] or 0)}
    if re.search(r"no tests ran|collected 0 items|No tests found", tail, re.I):
        return {"tests_total": 0, "tests_passed": 0, "tests_failed": 0, "tests_skipped": 0}
    passed = last_int(r"(\d+) (?:passed|passing)")
    failed = last_int(r"(\d+) (?:failed|failing)")
    skipped = last_int(r"(\d+) (?:skipped|pending)")
    errors = last_int(r"(\d+) errors?\b")
    if passed is not None or failed is not None:
        p, f, s, e = passed or 0, failed or 0, skipped or 0, errors or 0
        return {"tests_total": p + f + s + e, "tests_passed": p, "tests_failed": f + e, "tests_skipped": s}
    return res


def _safe_env() -> dict:
    # strip credentials, and test-runner context that would change nested runners' exit codes
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k) and not k.startswith("NODE_TEST")}
    env.update({"CI": "1", "FORCE_COLOR": "0", "NO_COLOR": "1", "PLAYWRIGHT_HTML_OPEN": "never"})
    return env


def _validate_command(cmd: str) -> str | None:
    if SHELL_META.search(cmd):
        return "command contains shell metacharacters; declare a project script instead"
    exe = shlex.split(cmd)[0]
    allowed = cfgmod.stack_registry()["allowed_executables"]
    if exe not in allowed:
        return f"executable '{exe}' is not in the allowed list"
    if not exe.startswith("./") and not shutil.which(exe):
        return f"executable '{exe}' not found on PATH"
    return None


def execute(check: dict, root: Path, log_dir: Path, label: str, timeout: int, fingerprint: str) -> dict:
    rec = {"name": check["name"], "kind": check["kind"], "cwd": check["cwd"], "source_fingerprint": fingerprint,
           "evidence_type": "executed"}
    if not check.get("command"):
        rec.update({"status": check.get("force_status", "NOT_RUN"), "reason": check.get("not_run_reason", "no command"),
                    "exit_code": None, "command": ""})
        return rec
    rec["command"] = check["command"]
    if check.get("network") and os.environ.get("DEVPIPELINE_OFFLINE") == "1":
        rec.update({"status": "NOT_RUN", "reason": "offline mode (DEVPIPELINE_OFFLINE=1): network check skipped", "exit_code": None})
        return rec
    problem = _validate_command(check["command"])
    if problem:
        rec.update({"status": "NOT_RUN" if "not found" in problem else "ERROR", "reason": problem, "exit_code": None})
        return rec
    cwd = (root / check["cwd"]).resolve()
    log = log_dir / f"{label}--{re.sub(r'[^A-Za-z0-9_.-]+', '_', check['name'])}.log"
    started = now_iso()
    t0 = time.monotonic()
    try:
        proc = subprocess.run(shlex.split(check["command"]), cwd=str(cwd), capture_output=True, text=True,
                              timeout=check.get("timeout") or timeout, env=_safe_env(), stdin=subprocess.DEVNULL)
        out = (proc.stdout or "") + ("\n--- stderr ---\n" + proc.stderr if proc.stderr else "")
        code = proc.returncode
        status = "PASS" if code == 0 else "FAIL"
    except subprocess.TimeoutExpired as exc:
        out = f"TIMEOUT after {exc.timeout}s\n" + str(exc.stdout or "")
        code, status = None, "ERROR"
    except OSError as exc:
        out, code, status = f"failed to start: {exc}", None, "ERROR"
    duration = int((time.monotonic() - t0) * 1000)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(f"$ {check['command']}\n# cwd: {cwd}\n# started: {started}\n# exit: {code}\n\n" + redact(out), encoding="utf-8")
    rec.update({"status": status, "exit_code": code, "started_at": started, "ended_at": now_iso(), "duration_ms": duration,
                "log_path": str(log)})
    if check["kind"] in ("unit", "integration", "api", "contract", "e2e", "accessibility", "ai_eval"):
        rec.update(parse_counts(out))
    if check.get("network"):
        rec["reason"] = "uses network (package registry advisory database)"
    return rec


def run(project: Path, run_id: str, label: str, target: Path | None = None, only: list[str] | None = None,
        baseline: bool = False) -> dict:
    project = Path(project).resolve()
    rd = project / ".devpipeline" / "runs" / run_id
    manifest = read_json(rd / "manifest.json")
    target = Path(target or manifest.get("source", {}).get("integration_worktree") or project).resolve()
    arch = read_json(project / ".devpipeline" / "architecture.json", default={}) if (project / ".devpipeline" / "architecture.json").exists() else {}
    timeout = manifest["config"].get("check_timeout_seconds", 900)
    fingerprint = gitutil.tree_fingerprint(target)
    checks = discover(target, arch)
    # install first so later checks have dependencies
    checks.sort(key=lambda c: (0 if c["kind"] == "install" else 1, c["name"]))
    if only:
        checks = [c for c in checks if c["kind"] in only or c["name"] in only or c["kind"] == "install"]
    results = []
    failed_install_dirs = set()
    if manifest.get("source", {}).get("skip_install"):
        for c in checks:
            if c["kind"] == "install":
                c.update({"command": None, "not_run_reason": "install skipped: audit of a non-git directory must not modify it"})
    for c in checks:
        if c["kind"] != "install" and c["cwd"] in failed_install_dirs and c.get("command"):
            results.append({"name": c["name"], "kind": c["kind"], "cwd": c["cwd"], "status": "BLOCKED", "exit_code": None,
                            "command": c["command"], "reason": "dependency install failed in this directory",
                            "source_fingerprint": fingerprint, "evidence_type": "executed"})
            continue
        r = execute(c, target, rd / "logs", label, timeout, fingerprint)
        if c["kind"] == "install" and r["status"] != "PASS":
            failed_install_dirs.add(c["cwd"])
        results.append(r)
    statuses = [r["status"] for r in results]
    overall = "FAIL" if "FAIL" in statuses else "ERROR" if "ERROR" in statuses else "PASS"
    report = {"run_id": run_id, "agent": "pipeline-operator", "kind": "baseline" if baseline else "checks",
              "source_fingerprint": fingerprint, "status": overall, "checks": results, "issues": [],
              "summary": f"{len(results)} checks: " + ", ".join(f"{s}={statuses.count(s)}" for s in sorted(set(statuses))),
              "created_at": now_iso()}
    name = f"{'baseline' if baseline else 'checks'}-{label}.json"
    atomic_write_json(rd / "test-reports" / name, report)
    import state_manager as sm  # record provenance: only coordinator-recorded check reports count as evidence
    with sm.mutate(project, run_id) as mm:
        mm.setdefault("check_reports", []).append(f"test-reports/{name}")
    return {"ok": True, "report": f"test-reports/{name}", "status": overall, "fingerprint": fingerprint,
            "checks": [{"name": r["name"], "kind": r["kind"], "status": r["status"],
                        **({"tests_total": r.get("tests_total"), "tests_skipped": r.get("tests_skipped")} if r.get("tests_total") is not None else {})}
                       for r in results]}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project")
    ap.add_argument("--run")
    ap.add_argument("--label", default="manual")
    ap.add_argument("--dir")
    ap.add_argument("--only")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--discover", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.discover:
            return emit({"ok": True, "checks": discover(Path(a.dir or ".").resolve())})
        if not (a.project and a.run):
            raise DPError("--project and --run are required")
        return emit(run(Path(a.project), a.run, a.label, Path(a.dir) if a.dir else None,
                        a.only.split(",") if a.only else None, a.baseline))
    except DPError as exc:
        return emit({"ok": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
