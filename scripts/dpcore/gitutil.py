"""Thin, safe git helpers. Never pushes, never force-resets user branches."""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

from .util import DPError

DOC_EXCLUDES = [":(exclude,glob)**/*.md", ":(exclude,glob)docs/**"]
FORBIDDEN = ("push", "reset --hard", "clean -fdx", "clean -fd", "branch -D")


def git(cwd: Path, *args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    joined = " ".join(args)
    if any(joined.startswith(f) for f in FORBIDDEN):
        raise DPError(f"refusing destructive git command: git {joined}")
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          env={**os.environ, **(env or {}), "GIT_TERMINAL_PROMPT": "0"})
    if check and proc.returncode != 0:
        raise DPError(f"git {joined} failed: {proc.stderr.strip()[:500]}")
    return proc


def is_repo(path: Path) -> bool:
    return git(path, "rev-parse", "--is-inside-work-tree", check=False).returncode == 0 if path.exists() else False


def toplevel(path: Path) -> Path:
    return Path(git(path, "rev-parse", "--show-toplevel").stdout.strip())


def head(path: Path) -> str | None:
    proc = git(path, "rev-parse", "HEAD", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def status_porcelain(path: Path) -> list[str]:
    out = git(path, "status", "--porcelain", "--untracked-files=all").stdout

    def local_env(line: str) -> bool:
        name = Path(line[3:].strip('"')).name
        return line.startswith("??") and name.startswith(".env") and name not in (".env.example", ".env.sample", ".env.template")

    return [l for l in out.splitlines() if l.strip() and not l[3:].startswith(".devpipeline") and not local_env(l)]


def changed_files(path: Path, base: str) -> list[str]:
    """Files changed in the working tree of `path` relative to `base` (incl. untracked)."""
    files = set()
    diff = git(path, "diff", "--name-only", base, check=False).stdout
    files |= {l.strip() for l in diff.splitlines() if l.strip() and not l.startswith(".devpipeline")}
    untracked = git(path, "ls-files", "--others", "--exclude-standard", check=False).stdout
    files |= {l.strip() for l in untracked.splitlines() if l.strip() and not l.startswith(".devpipeline")}
    return sorted(files)


def pathspec_excludes(patterns: list[str]) -> list[str]:
    """Convert ownership-style patterns ('tests/', '**/*.test.*') into git exclude pathspecs."""
    out = []
    for p in patterns:
        p = p.strip()
        if not p:
            continue
        glob = p + "**" if p.endswith("/") else p
        out.append(f":(exclude,glob){glob}")
        if not p.startswith("**/") and not p.endswith("/"):
            out.append(f":(exclude,glob)**/{p}")
    return out


def tree_fingerprint(path: Path, include_docs: bool = False, extra_excludes: list[str] | None = None) -> str:
    """Content fingerprint of the working tree (tracked + untracked, honouring .gitignore).

    Uses a temporary index so the user's index is never touched. Docs are
    excluded by default so documentation-only edits do not invalidate test
    evidence (they are covered by the separate documentation gate).
    """
    if is_repo(path):
        with tempfile.TemporaryDirectory() as tmp:
            # fresh empty index: stage every non-ignored file, then hash the tree
            env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            spec = ["."] + ([] if include_docs else DOC_EXCLUDES) + pathspec_excludes(extra_excludes or [])
            git(path, "add", "-A", "--", *spec, env=env)
            return git(path, "write-tree", env=env).stdout.strip()
    h = hashlib.sha256()
    for f in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = f.relative_to(path).as_posix()
        if rel.startswith((".devpipeline/", ".git/", "node_modules/")) or "/node_modules/" in rel:
            continue
        if not include_docs and (rel.endswith(".md") or rel.startswith("docs/")):
            continue
        if extra_excludes:
            from .dag import path_owned
            if path_owned(rel, extra_excludes):
                continue
        h.update(rel.encode())
        h.update(f.read_bytes())
    return "sha256:" + h.hexdigest()
