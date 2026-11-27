"""Shared utilities: time, atomic JSON IO, locking, checksums, payload decoding."""
from __future__ import annotations

import base64
import contextlib
import datetime as _dt
import fcntl
import hashlib
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator


class DPError(Exception):
    """Expected, user-facing failure. CLI prints {"ok": false, "error": ...}."""


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def new_run_id() -> str:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"run-{stamp}-{secrets.token_hex(3)}"


def read_json(path: Path | str, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        if default is not None:
            return default
        raise DPError(f"missing file: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DPError(f"invalid JSON in {p}: {exc}") from exc


def atomic_write_text(path: Path | str, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def atomic_write_json(path: Path | str, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n")


@contextlib.contextmanager
def file_lock(lock_path: Path | str, timeout_s: float = 60.0) -> Iterator[None]:
    """Exclusive advisory lock (fcntl). Serialises writers of shared state."""
    import time

    p = Path(lock_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fh = open(p, "a+")
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise DPError(f"timed out waiting for lock {p}")
                time.sleep(0.05)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Operator output integrity. The workflow runtime cannot run commands, so the
# pipeline-operator agent relays our stdout. We attach a checksum computed over
# canonical JSON; workflows/lib recompute it (same algorithm) and reject relays
# that were altered or fabricated.
# ---------------------------------------------------------------------------

def _sanitize(value: Any) -> Any:
    if isinstance(value, float):
        return int(value) if value.is_integer() else str(value)
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_sanitize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fnv1a32(text: str) -> str:
    h = 0x811C9DC5
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def with_checksum(payload: dict) -> dict:
    clean = _sanitize(payload)
    clean.pop("checksum", None)
    clean["checksum"] = fnv1a32(canonical_json(clean))
    return clean


def emit(payload: dict, code: int = 0) -> int:
    sys.stdout.write(json.dumps(with_checksum(payload), ensure_ascii=True, sort_keys=True) + "\n")
    sys.stdout.flush()
    return code


def decode_b64_json(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001 - surface as DPError
        raise DPError(f"invalid base64 JSON payload: {exc}") from exc


def rel_posix(path: Path, base: Path) -> str:
    return path.resolve().relative_to(base.resolve()).as_posix()
