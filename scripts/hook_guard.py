#!/usr/bin/env python3
"""DevPipeline hook guard (defense in depth; the workflow + gate stay authoritative).

Invoked by hooks/hooks.json:  hook_guard.py pre|post|subagent-start|subagent-stop
Reads the hook JSON from stdin.

Scope: it acts ONLY when (a) the tool call comes from a DevPipeline subagent
(agent_type "devpipeline:<name>") and (b) the project has an active run
(.devpipeline/active.json). Everything else exits 0 immediately, so unrelated
sessions and projects are unaffected. It never approves anything — it can only
deny (PreToolUse) or report (PostToolUse); Claude Code permissions still apply.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
PREFIX = "devpipeline:"
READ_ONLY_AGENTS = {"code-reviewer", "final-reviewer", "orchestrator"}
try:  # single source: config/agent-registry.json (report_kind marks QA agents)
    QA_AGENTS = {a for a, d in json.loads((SCRIPTS.parent / "config" / "agent-registry.json").read_text())["agents"].items() if d.get("report_kind")}
except (OSError, ValueError, KeyError):
    QA_AGENTS = {"code-qa-engineer", "browser-qa-engineer", "security-engineer", "ai-evaluation-engineer", "performance-engineer"}
PLANNING = {
    "product-analyst": ["requirements.json", "PRD.md", "acceptance-matrix.md"],
    "software-architect": ["architecture.json", "ARCHITECTURE.md", "tasks.json", "adr/", "contracts/", "contract-changes.json"],
    "ui-ux-designer": ["DESIGN_SYSTEM.md"],
}
RUN_WRITABLE = ["agent-results/", "test-reports/", "reviews/", "screenshots/", "logs/", "artifacts/"]
DOC_SCOPE = ["*.md", "**/*.md", "docs/", ".env.example", "LICENSE"]
TEMP_ROOTS = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")


def temp_roots() -> tuple[str, ...]:
    """POSIX temp roots plus the platform temp dir (Windows: %TEMP%), all as POSIX-style prefixes."""
    import tempfile
    try:
        extra = Path(tempfile.gettempdir()).resolve().as_posix().rstrip("/") + "/"
    except OSError:
        return TEMP_ROOTS
    return TEMP_ROOTS + (extra,)

UNSAFE = [
    (r"\bgit\s+push\b", "pushing to remotes is never automatic"),
    (r"\bgit\s+reset\s+--hard\b", "destructive git reset"),
    (r"\bgit\s+clean\s+-[a-z]*f", "destructive git clean"),
    (r"\bgit\s+branch\s+-D\b", "force branch deletion"),
    (r"\bgit\s+checkout\s+(--\s+)?\.\s*$", "discarding working tree changes"),
    (r"\bgit\s+restore\s+(--staged\s+)?\.", "discarding working tree changes"),
    (r"\bgit\s+(worktree\s+remove|rebase|filter-branch)\b", "worktree/history management is reserved for the coordinator"),
    (r"--dangerously-skip-permissions|bypassPermissions|--allow-dangerously-skip-permissions", "permission bypass is forbidden"),
    (r"\bsudo\b", "system-wide changes require the user"),
    (r"\b(npm|pnpm|yarn)\s+(i|install|add)\s+.*(-g\b|--global\b)", "global package installs require approval"),
    (r"\bbrew\s+(install|upgrade|uninstall)\b", "system package changes require approval"),
    (r"(^|[;&|]\s*)(pip3?|python3?\s+-m\s+pip)\s+install\b(?!.*(--target|--prefix))", "use the project's virtualenv/uv instead of a system-wide pip install"),
    (r"\b(npm|pnpm|yarn)\s+publish\b|\bdocker\s+push\b|\btwine\s+upload\b", "publishing artifacts requires explicit approval"),
    (r"\bdocker\s+(system|volume|image|container)\s+prune\b|\bdocker\s+volume\s+rm\b", "pruning Docker resources may destroy user data"),
    (r"\b(vercel|netlify|fly|railway)\b.*\b(--prod|deploy)\b|\bterraform\s+(apply|destroy)\b|\bkubectl\s+(apply|delete|create)\b|\bhelm\s+(install|upgrade|uninstall)\b",
     "deployments / cloud resources require explicit approval"),
    (r"\b(aws|gcloud|az)\s+\S+\s+(create|delete|deploy|put|update)\b", "cloud resource changes require explicit approval"),
    (r"(?i)\bdrop\s+(database|schema)\b|\btruncate\s+table\b", "destructive database statement"),
    (r"\bprisma\s+migrate\s+reset\b|\bprisma\s+db\s+push\s+.*--force-reset|\balembic\s+downgrade\b|\bflyway\s+clean\b", "destructive migration requires approval"),
    (r"(curl|wget)\s+[^|]*\|\s*(sudo\s+)?(ba|z)?sh\b", "piping remote scripts to a shell is forbidden"),
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+(/|~|\$HOME)(\s|$)", "recursive delete of root/home"),
    (r"(>|>>|tee\s+)\s*\S*\.devpipeline/(state\.json|active\.json|config\.json|runs/[^/\s]+/(manifest\.json|events\.jsonl|tasks\.json))",
     "coordinator-owned state may only be changed by scripts/state_manager.py"),
]


def out_deny(reason: str) -> int:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": f"DevPipeline guard: {reason}"}}))
    return 0


def find_project(*paths: str | None) -> Path | None:
    for p in paths:
        if not p:
            continue
        cur = Path(p).resolve()
        for cand in [cur] + list(cur.parents):
            if (cand / ".devpipeline" / "active.json").is_file():
                return cand
    return None


def state_dir() -> Path:
    return Path(os.environ.get("DEVPIPELINE_STATE_DIR", Path.home() / ".local" / "state" / "devpipeline"))


def registry_projects() -> list[Path]:
    """Projects with an active DevPipeline run, most recently updated first."""
    try:
        reg = json.loads((state_dir() / "active-runs.json").read_text())
    except (OSError, json.JSONDecodeError):
        return []
    items = sorted(reg.get("runs", {}).items(), key=lambda kv: kv[1].get("updated_at", ""), reverse=True)
    return [Path(k) for k, _ in items if (Path(k) / ".devpipeline" / "active.json").is_file()]


def resolve_project(data: dict) -> Path | None:
    tool_input = data.get("tool_input") or {}
    direct = find_project(tool_input.get("file_path") or tool_input.get("notebook_path"), data.get("cwd"),
                          os.environ.get("CLAUDE_PROJECT_DIR"))
    if direct:
        return direct
    active = registry_projects()
    command = tool_input.get("command") or ""
    for proj in active:  # a command that names an active project's path belongs to it
        if str(proj) in command:
            return proj
    return active[0] if active else None


def trace(data: dict, mode: str) -> None:
    if os.environ.get("DEVPIPELINE_HOOK_TRACE") != "1":
        return
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        with open(state_dir() / "hook-trace.jsonl", "a") as fh:
            fh.write(json.dumps({"mode": mode, "event": data.get("hook_event_name"), "agent_type": data.get("agent_type"),
                                 "tool": data.get("tool_name"), "cwd": data.get("cwd"),
                                 "command": ((data.get("tool_input") or {}).get("command") or "")[:200]}) + "\n")
    except OSError:
        pass


def load_active(project: Path) -> dict | None:
    try:
        act = json.loads((project / ".devpipeline" / "active.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if act.get("phase") in ("COMPLETED", "CANCELLED", "FAILED"):
        return None
    return act


def owned(rel: str, patterns: list[str]) -> bool:
    sys.path.insert(0, str(SCRIPTS))
    from dpcore.dag import path_owned  # local import keeps the fast path cheap

    return path_owned(rel, patterns)


def check_write(agent: str, file_path: str, project: Path, act: dict) -> str | None:
    fp = Path(file_path).resolve()
    try:
        rel = fp.relative_to(project.resolve()).as_posix()
    except ValueError:
        if any(fp.as_posix().startswith(t) for t in temp_roots()):
            return None  # scratch files outside the project (never inside it)
        return f"{agent} may not write outside the project ({fp})"
    if rel.startswith(".devpipeline/"):
        sub = rel[len(".devpipeline/"):]
        if sub.startswith("worktrees/"):
            parts = sub.split("/", 3)
            if len(parts) < 4:
                return "invalid worktree path"
            _, run, slot, inner = parts
            if slot == "audit":
                return "audit mode is read-only"
            if slot == "integration":
                if agent == "integration-engineer":
                    return None
                if agent in QA_AGENTS and owned(inner, act.get("qa_write_paths", [])):
                    return None
                if agent == "technical-writer" and owned(inner, DOC_SCOPE):
                    return None
                return f"{agent} may not modify '{inner}' in the integration worktree (QA: test paths only; writer: docs only)"
            task = act.get("active_tasks", {}).get(slot)
            if not task:
                return f"task {slot} is not active"
            if task["agent"] != agent:
                return f"task {slot} belongs to {task['agent']}, not {agent}"
            if not owned(inner, task.get("owned_paths", [])):
                return f"'{inner}' is outside the owned paths of {slot}: {task.get('owned_paths')}"
            return None
        if sub.startswith("runs/"):
            parts = sub.split("/", 2)
            if len(parts) == 3 and any(parts[2].startswith(w) for w in RUN_WRITABLE):
                if parts[2].startswith("reviews/") and agent not in ("code-reviewer", "final-reviewer"):
                    return "only reviewers write review verdicts"
                if parts[2].startswith(("test-reports/checks-", "test-reports/baseline-")):
                    return "deterministic check reports are written only by run_checks.py"
                return None
            return f"'{sub}' is coordinator-owned run state"
        for allowed in PLANNING.get(agent, []):
            if sub == allowed or (allowed.endswith("/") and sub.startswith(allowed)):
                return None
        return f"{agent} may not write .devpipeline/{sub}"
    # main checkout
    if act.get("mode") == "audit":
        return "audit mode is read-only for project source"
    for tid, task in act.get("active_tasks", {}).items():
        if Path(task.get("worktree", "")).resolve() == project.resolve() and task["agent"] == agent:
            if owned(rel, task.get("owned_paths", [])):
                return None
            return f"'{rel}' is outside the owned paths of {tid}"
    for prot in act.get("protected_paths", []):
        if owned(rel, [prot]):
            return f"'{rel}' is protected"
    return f"{agent} must work inside its assigned worktree, not the user's checkout ({rel})"


def _outside_single_quotes(command: str) -> str:
    """The parts of a POSIX shell command that are NOT inside single quotes (backslash escapes outside
    quotes are consumed), so literal text such as a quoted reason with '|' is not mistaken for an operator."""
    out, i, n = [], 0, len(command)
    while i < n:
        ch = command[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "'":
            end = command.find("'", i + 1)
            if end < 0:
                return command  # unbalanced quote: treat everything as unquoted (deny path)
            i = end + 1
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def is_devpipeline_helper(command: str) -> bool:
    """`python3 <devpipeline plugin root>/scripts/<helper>.py args...` and nothing else.

    The plugin may be loaded from its source folder or from the plugin cache, so the
    root is recognised by its manifest rather than by one fixed path.
    """
    import shlex

    unquoted = _outside_single_quotes(command)  # the engine single-quotes every free-text argument
    if re.search(r"[;&|`<>\n]|\$\(|\$\{", unquoted):
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if len(argv) < 2 or argv[0] != "python3":
        return False
    script = Path(argv[1])
    if not re.fullmatch(r"[a-z_]+\.py", script.name) or script.parent.name != "scripts" or not script.is_file():
        return False
    manifest = script.parent.parent / ".claude-plugin" / "plugin.json"
    try:
        return json.loads(manifest.read_text()).get("name") == "devpipeline"
    except (OSError, json.JSONDecodeError):
        return False


WRITE_CMD = re.compile(r"(>>?|\btee\b|\bsed\s+-i|\bmv\b|\bcp\b|\brm\b|\btouch\b|\bmkdir\b|\bln\b|\bchmod\b|\btruncate\b|\bdd\b)")


def bash_writes_outside(command: str, project: Path | None) -> str | None:
    """Heuristic: a write-like command that names the user's checkout (outside .devpipeline/) is denied.

    Bash cannot be parsed reliably; the deterministic backstop is the user-checkout
    comparison in devpipeline.py (finish-tasks / finalize) and the per-task git diff.
    """
    if not project or not WRITE_CMD.search(command):
        return None
    resolved = str(project.resolve())
    roots = {str(project), resolved}
    if resolved.startswith("/private/"):
        roots.add(resolved[len("/private"):])  # macOS /var, /tmp aliases
    for root in sorted(roots, key=len, reverse=True):
        for m in re.finditer(re.escape(root) + r"(/[^\s'\"]*)?(?=$|[\s'\";&|)])", command):
            tail = m.group(1) or ""
            if not tail.startswith("/.devpipeline/"):
                return f"write-like shell command targets the user's checkout ({root}{tail}); work only in your worktree"
    return None


def check_bash(agent: str, command: str, act: dict, project: Path | None = None) -> str | None:
    if agent == "pipeline-operator":
        return None if is_devpipeline_helper(command) else "pipeline-operator may only run a single DevPipeline helper script"
    for pattern, why in UNSAFE:
        if re.search(pattern, command):
            return why
    outside = bash_writes_outside(command, project)
    if outside:
        return outside
    if act.get("mode") == "audit" and re.search(r"(^|\s)(rm|mv|sed\s+-i|truncate)\s", command):
        return "audit mode is read-only"
    return None


def record(project: Path, act: dict, etype: str, data: dict) -> None:
    run = act.get("run_id")
    if not run:
        return
    path = project / ".devpipeline" / "runs" / run / "events.jsonl"
    sys.path.insert(0, str(SCRIPTS))
    from dpcore.util import now_iso
    from redact_secrets import redact

    rec = {"run_id": run, "ts": now_iso(), "event": etype, "agent": data.get("agent_type", "")[len(PREFIX):],
           "detail": (data.get("last_assistant_message") or "")[:300] or None, "source": "hook"}
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(redact(json.dumps({k: v for k, v in rec.items() if v is not None})) + "\n")
    except OSError:
        pass


def validate_artifact(project: Path, file_path: str) -> str | None:
    fp = Path(file_path)
    try:
        rel = fp.resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        return None
    schema = None
    if re.match(r"\.devpipeline/runs/[^/]+/agent-results/[^/]+\.json$", rel):
        schema = "agent-result.schema.json"
    elif re.match(r"\.devpipeline/runs/[^/]+/test-reports/[^/]+\.json$", rel):
        schema = "test-report.schema.json"
    elif re.match(r"\.devpipeline/runs/[^/]+/reviews/[^/]+\.json$", rel):
        schema = "review.schema.json"
    elif rel in (".devpipeline/requirements.json", ".devpipeline/architecture.json", ".devpipeline/tasks.json"):
        schema = {"requirements": "requirements.schema.json", "architecture": "architecture.schema.json", "tasks": "task.schema.json"}[fp.stem]
    if not schema:
        return None
    sys.path.insert(0, str(SCRIPTS))
    from dpcore.jsonschema_lite import validate_named
    from redact_secrets import find, redact

    text = fp.read_text(errors="replace")
    if find(text):
        fp.write_text(redact(text))
        return "secret-looking values were redacted from the artifact; never write credentials into artifacts"
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        return f"{rel} is not valid JSON: {exc}"
    errs = validate_named(doc, schema)
    if errs:
        return f"{rel} violates {schema}: " + "; ".join(errs[:5])
    return None


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "pre"
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    trace(data, mode)
    agent_type = data.get("agent_type") or ""
    if not agent_type.startswith(PREFIX):
        return 0  # fast path: not a DevPipeline subagent
    agent = agent_type[len(PREFIX):]
    tool_input = data.get("tool_input") or {}
    project = resolve_project(data)
    if not project:
        return 0
    act = load_active(project)
    if not act:
        return 0
    try:
        if mode in ("subagent-start", "subagent-stop"):
            record(project, act, "AGENT_STARTED" if mode == "subagent-start" else "AGENT_STOPPED", data)
            return 0
        tool = data.get("tool_name", "")
        if mode == "pre":
            if tool == "Bash":
                why = check_bash(agent, tool_input.get("command", ""), act, project)
            elif tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                target = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
                why = check_write(agent, target, project, act) if target else None
                # as_posix: on Windows str() has backslashes, which denied reviewers their own verdict files
                if why is None and agent in READ_ONLY_AGENTS and ".devpipeline/runs/" not in Path(target).resolve().as_posix():
                    why = f"{agent} is read-only"
            else:
                why = None
            return out_deny(why) if why else 0
        if mode == "post" and tool in ("Write", "Edit", "MultiEdit"):
            problem = validate_artifact(project, tool_input.get("file_path", ""))
            if problem:
                sys.stderr.write(f"DevPipeline: {problem}. Fix the file so it validates.\n")
                return 2  # PostToolUse: shown to the agent as feedback
    except Exception as exc:  # noqa: BLE001 — never break the session because of the guard
        sys.stderr.write(f"DevPipeline guard internal error (allowing): {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
