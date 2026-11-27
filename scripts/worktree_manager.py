#!/usr/bin/env python3
"""Git worktree isolation for parallel writing tasks.

Layout (inside the target project, git-ignored):
  .devpipeline/worktrees/<run>/integration   branch devpipeline/<run>/integration (integration)
  .devpipeline/worktrees/<run>/<TASK-ID>     branch devpipeline/<run>/<TASK>   (one per task attempt)

Guarantees:
  * the user's checkout, index and uncommitted work are never touched
    (exception: `finalize` fast-forwards the main checkout of a project that
    DevPipeline itself created, and only when that checkout is clean);
  * every task starts from the current integration HEAD (recorded as base);
  * only DevPipeline-owned branches/worktrees are created or removed;
  * nothing is pushed; no force operations on user branches.

Non-git existing projects fall back to `serial` isolation: writing tasks run one
at a time in the project directory and changed files are detected by content
snapshots (documented reduced guarantee).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dpcore import gitutil  # noqa: E402
from dpcore.util import DPError, atomic_write_json, emit, read_json  # noqa: E402

IDENT = ["-c", "user.name=DevPipeline", "-c", "user.email=devpipeline@localhost"]


def wt_root(project: Path, run_id: str) -> Path:
    return Path(project).resolve() / ".devpipeline" / "worktrees" / run_id


def branch_for(run_id: str, task_id: str | None = None) -> str:
    """devpipeline/<run>/integration for integration, devpipeline/<run>/<TASK> per task (sibling refs)."""
    return f"devpipeline/{run_id}/" + (task_id or "integration")


def _commit_all(cwd: Path, message: str) -> str | None:
    gitutil.git(cwd, "add", "-A")  # .devpipeline/ is always git-ignored (see state_manager._ensure_gitignore)
    staged = gitutil.git(cwd, "diff", "--cached", "--name-only", check=False).stdout.split()
    secrets = [f for f in staged if Path(f).name.startswith(".env") and Path(f).name not in (".env.example", ".env.sample", ".env.template")]
    if secrets:  # environment files with real values stay local
        gitutil.git(cwd, "restore", "--staged", "--", *secrets, check=False)
    if gitutil.git(cwd, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return None
    gitutil.git(cwd, *IDENT, "commit", "-q", "--no-verify", "-m", message)
    return gitutil.head(cwd)


def setup_integration(project: Path, run_id: str, project_mode: str) -> dict:
    project = Path(project).resolve()
    warnings: list[str] = []
    if not gitutil.is_repo(project):
        if project_mode != "new":
            return {"isolation": "serial", "integration_worktree": str(project), "base_revision": None,
                    "warnings": ["existing project is not a git repository: worktree isolation unavailable; "
                                 "writing tasks are serialised and changes detected by content snapshots"]}
        gitutil.git(project, "init", "-q", "-b", "main")
    if gitutil.head(project) is None:
        _commit_all(project, "chore: initial commit (DevPipeline)")
        if gitutil.head(project) is None:
            gitutil.git(project, *IDENT, "commit", "-q", "--no-verify", "--allow-empty", "-m", "chore: initial commit (DevPipeline)")
    if gitutil.status_porcelain(project):
        warnings.append("the main checkout has uncommitted changes; they are preserved and NOT visible to DevPipeline tasks")
    base = gitutil.head(project)
    path = wt_root(project, run_id) / "integration"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        gitutil.git(project, "worktree", "add", "-q", "-B", branch_for(run_id), str(path), base)
    return {"isolation": "worktree", "integration_worktree": str(path), "integration_branch": branch_for(run_id),
            "base_revision": base, "integration_head": gitutil.head(path), "warnings": warnings}


def setup_audit(project: Path, run_id: str) -> dict:
    """Audit runs against a detached worktree of HEAD so installs/tests never touch the user's checkout."""
    project = Path(project).resolve()
    if not gitutil.is_repo(project) or gitutil.head(project) is None:
        return {"isolation": "read-only", "integration_worktree": str(project), "integration_head": None, "skip_install": True,
                "warnings": ["not a git repository: audit runs in place; dependency installs are skipped to avoid modifying the directory"]}
    path = wt_root(project, run_id) / "audit"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        gitutil.git(project, "worktree", "add", "-q", "--detach", str(path), "HEAD")
    warnings = ["uncommitted changes in the main checkout are not part of the audited revision"] if gitutil.status_porcelain(project) else []
    return {"isolation": "read-only", "integration_worktree": str(path), "integration_head": gitutil.head(path),
            "base_revision": gitutil.head(path), "warnings": warnings}


def checkout_state(project: Path) -> dict:
    """HEAD + porcelain status of the user's main checkout (DevPipeline never writes there during a run)."""
    project = Path(project).resolve()
    if not gitutil.is_repo(project):
        return {}
    status = gitutil.status_porcelain(project)
    hashes = {}
    for line in status:  # a second edit to an already-dirty file changes its hash, not its status line
        rel = line[3:].split(" -> ")[-1].strip('"')
        f = project / rel
        if f.is_file() and f.stat().st_size < 50_000_000:
            hashes[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
    return {"head": gitutil.head(project), "status": status, "hashes": hashes}


def snapshot(root: Path) -> dict[str, str]:
    out = {}
    for f in sorted(root.rglob("*")):
        rel = f.relative_to(root).as_posix()
        if not f.is_file() or rel.startswith((".devpipeline/", ".git/")) or "node_modules/" in rel:
            continue
        out[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def create_task(project: Path, run_id: str, task_id: str, integration: str, isolation: str) -> dict:
    project = Path(project).resolve()
    if isolation != "worktree":
        snap = snapshot(Path(integration))
        snap_path = project / ".devpipeline" / "runs" / run_id / "artifacts" / f"snapshot-{task_id}.json"
        atomic_write_json(snap_path, snap)
        return {"task_id": task_id, "worktree": str(integration), "branch": None, "base_revision": None, "snapshot": str(snap_path)}
    base = gitutil.head(Path(integration))
    path = wt_root(project, run_id) / task_id
    if path.exists():
        gitutil.git(project, "worktree", "remove", "--force", str(path))  # our own previous attempt
    gitutil.git(project, "worktree", "add", "-q", "-B", branch_for(run_id, task_id), str(path), base)
    return {"task_id": task_id, "worktree": str(path), "branch": branch_for(run_id, task_id), "base_revision": base}


def finish_task(project: Path, run_id: str, task: dict, isolation: str) -> dict:
    """Commit a task's work on its branch; return the actually changed files."""
    if isolation != "worktree":
        before = read_json(task["snapshot"])
        after = snapshot(Path(task["worktree"]))
        changed = sorted({k for k in set(before) | set(after) if before.get(k) != after.get(k)})
        return {"changed_files": changed, "result_revision": None}
    wt = Path(task["worktree"])
    if not wt.exists():
        return {"changed_files": [], "result_revision": None, "error": "worktree missing"}
    changed = gitutil.changed_files(wt, task["base_revision"])
    rev = _commit_all(wt, f"devpipeline({run_id}): {task['task_id']}") or gitutil.head(wt)
    return {"changed_files": changed, "result_revision": rev}


def merge_task(integration: Path, run_id: str, task_id: str) -> dict:
    branch = branch_for(run_id, task_id)
    proc = gitutil.git(integration, *IDENT, "merge", "--no-ff", "--no-edit", "-q", branch, check=False)
    if proc.returncode == 0:
        return {"task_id": task_id, "merged": True, "head": gitutil.head(integration)}
    conflicts = gitutil.git(integration, "diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
    gitutil.git(integration, "merge", "--abort", check=False)
    return {"task_id": task_id, "merged": False, "conflicts": conflicts, "error": proc.stderr.strip()[:300] or proc.stdout.strip()[:300]}


def commit_integration(integration: Path, message: str) -> dict:
    if not gitutil.is_repo(integration):
        return {"committed": False, "changed_files": [], "head": None}
    base = gitutil.head(integration)
    changed = gitutil.changed_files(integration, base) if base else []
    rev = _commit_all(integration, message)
    return {"committed": rev is not None, "changed_files": changed, "head": gitutil.head(integration)}


def finalize(project: Path, run_id: str, project_mode: str, merged_tasks: list[str], fast_forward: bool) -> dict:
    project = Path(project).resolve()
    removed, kept = [], []
    root = wt_root(project, run_id)
    for task_id in merged_tasks:
        path = root / task_id
        if path.exists():
            if gitutil.status_porcelain(path):
                kept.append(task_id)
                continue
            gitutil.git(project, "worktree", "remove", str(path), check=False)
            gitutil.git(project, "branch", "-d", branch_for(run_id, task_id), check=False)  # safe delete only
            removed.append(task_id)
    result = {"removed_task_worktrees": removed, "kept_dirty_worktrees": kept, "fast_forwarded": False}
    integ_branch = branch_for(run_id)
    if fast_forward and project_mode == "new" and gitutil.is_repo(project):
        if not gitutil.status_porcelain(project):
            proc = gitutil.git(project, "merge", "--ff-only", "-q", integ_branch, check=False)
            result["fast_forwarded"] = proc.returncode == 0
            if proc.returncode != 0:
                result["note"] = "fast-forward not possible; integration branch kept"
        else:
            result["note"] = "main checkout dirty; not fast-forwarded"
    result["integration_branch"] = integ_branch
    result["merge_hint"] = f"git merge --no-ff {integ_branch}   # review first: git log --oneline HEAD..{integ_branch}"
    return result


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["setup", "list"])
    ap.add_argument("--project", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--project-mode", default="existing", choices=["new", "existing"])
    a = ap.parse_args(argv)
    try:
        if a.command == "setup":
            return emit({"ok": True, **setup_integration(Path(a.project), a.run, a.project_mode)})
        out = gitutil.git(Path(a.project), "worktree", "list", "--porcelain").stdout
        return emit({"ok": True, "worktrees": [l[9:] for l in out.splitlines() if l.startswith("worktree ") and a.run in l]})
    except DPError as exc:
        return emit({"ok": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
