#!/usr/bin/env python3
"""Secret detection and redaction.

    redact_secrets.py redact <file|->        print redacted text
    redact_secrets.py scan <dir> [--json]    exit 1 when likely secrets are found

Used for: log redaction before persisting evidence, the built-in `secret-scan`
check, and the hook guard (artifacts written by agents).
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

PATTERNS = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{32,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|github_pat_[A-Za-z0-9_]{60,}")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    ("stripe_key", re.compile(r"\b(?:sk|rk)_live_[0-9a-zA-Z]{20,}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("url_credentials", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]{3,}@[^\s]+", re.I)),
]
ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY)[A-Z0-9_]*)\s*[=:]\s*[\"']?([^\s\"'#]{8,})"
)
PLACEHOLDER = re.compile(r"(?i)^(?:<.*>|\$\{.*\}|\$[A-Z_]+|x{4,}|\*{4,}|changeme|change-me|example|placeholder|your[-_].*|dummy.*|test.*|fake.*|redacted.*|none|null|todo|\.\.\.)$")
SKIP_DIRS = {".git", "node_modules", ".devpipeline", ".venv", "venv", "dist", "build", ".next", "__pycache__", "target", "coverage"}
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".lock", ".woff", ".woff2", ".ttf", ".mp4", ".sqlite", ".db"}


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    freq = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in freq.values())


def find(text: str) -> list[dict]:
    hits = []
    for name, rx in PATTERNS:
        for m in rx.finditer(text):
            hits.append({"type": name, "start": m.start(), "end": m.end()})
    for m in ASSIGNMENT.finditer(text):
        value = m.group(2)
        if PLACEHOLDER.match(value) or value.lower().startswith(("process.env", "os.environ", "env(", "getenv")):
            continue
        if _entropy(value) < 3.0:
            continue
        hits.append({"type": "assignment:" + m.group(1), "start": m.start(2), "end": m.end(2)})
    return hits


def redact(text: str) -> str:
    hits = sorted(find(text), key=lambda h: h["start"], reverse=True)
    last = None
    for h in hits:
        if last is not None and h["end"] > last:
            continue
        text = text[: h["start"]] + f"[REDACTED:{h['type'].split(':')[0]}]" + text[h["end"]:]
        last = h["start"]
    return text


def _candidate_files(root: Path) -> list[Path]:
    """Files that would be committed: git-tracked + untracked-not-ignored; else a filtered walk."""
    import subprocess

    proc = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=str(root),
                          capture_output=True, text=True)
    if proc.returncode == 0:
        return [root / l for l in sorted(set(proc.stdout.splitlines())) if l.strip()]
    return sorted(root.rglob("*"))


def scan(root: Path) -> list[dict]:
    findings = []
    for f in _candidate_files(root):
        if not f.is_file() or any(part in SKIP_DIRS for part in f.relative_to(root).parts):
            continue
        if f.suffix.lower() in SKIP_SUFFIX or f.stat().st_size > 2_000_000:
            continue
        rel = f.relative_to(root).as_posix()
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for h in find(text):
            line = text.count("\n", 0, h["start"]) + 1
            findings.append({"file": rel, "line": line, "type": h["type"]})
        if f.name == ".env" or (f.name.startswith(".env.") and f.name not in (".env.example", ".env.sample", ".env.template")):
            findings.append({"file": rel, "line": 0, "type": "env_file_present"})
    return findings


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("redact", "scan"):
        print(__doc__)
        return 2
    if argv[0] == "redact":
        src = sys.stdin.read() if argv[1] == "-" else Path(argv[1]).read_text(errors="replace")
        sys.stdout.write(redact(src))
        return 0
    findings = scan(Path(argv[1]))
    if "--json" in argv:
        print(json.dumps({"findings": findings, "count": len(findings)}))
    else:
        for f in findings:
            print(f"{f['file']}:{f['line']}: {f['type']}")
        print(f"secret-scan: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
