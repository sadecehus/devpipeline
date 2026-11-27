"""Shared helpers for DevPipeline unit tests (stdlib unittest)."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["DEVPIPELINE_OFFLINE"] = "1"
os.environ["DEVPIPELINE_STATE_DIR"] = tempfile.mkdtemp(prefix="dp-state-")  # never touch the real registry


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="dp-unit-"))


def write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data, indent=2))
    return path


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def node_project(d: Path, test_body: str = "test('ok', () => assert.ok(true))\n", scripts=None) -> None:
    write(d / "package.json", {"name": "p", "version": "1.0.0", "scripts": scripts or {"test": "node --test test/", "build": "node --check index.js"}})
    write(d / "package-lock.json", {"name": "p", "version": "1.0.0", "lockfileVersion": 3, "requires": True, "packages": {"": {"name": "p", "version": "1.0.0"}}})
    write(d / ".npmrc", "audit=false\nfund=false\n")
    write(d / "index.js", "module.exports = 1\n")
    write(d / "test" / "a.test.js", "const test = require('node:test')\nconst assert = require('node:assert')\n" + test_body)


def task(tid, agent="backend-engineer", deps=(), owned=("src/",), status="PENDING", **kw):
    return {"id": tid, "agent": agent, "title": tid, "requirements": [], "dependencies": list(deps), "owned_paths": list(owned),
            "acceptance_criteria": [], "status": status, "attempt": 0, **kw}


def requirements(ai=False, frontend=False, extra_ac=None):
    reqs = [{"id": "REQ-001", "title": "Add", "priority": "must", "type": "functional", "description": "adds",
             "acceptance_criteria": [{"id": "AC-001", "description": "add returns the sum", "verification_method": "unit"}]}]
    if extra_ac:
        reqs[0]["acceptance_criteria"].append(extra_ac)
    return {"project_title": "T", "summary": "s", "requirements": reqs, "assumptions": [], "non_goals": [],
            "features": {"backend": True, "ai": ai, "frontend": frontend}}
