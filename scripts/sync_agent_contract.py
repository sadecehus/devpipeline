#!/usr/bin/env python3
"""Copy the common agent contract (docs/AGENT_CONTRACT.md) into every agents/*.md.

  sync_agent_contract.py          rewrite agents in place
  sync_agent_contract.py --check  exit 1 if any agent copy differs (used by tests/CI)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START, END = "<!-- common-contract:start -->", "<!-- common-contract:end -->"
BLOCK = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)


def canonical() -> str:
    text = (ROOT / "docs" / "AGENT_CONTRACT.md").read_text()
    m = BLOCK.search(text)
    if not m:
        raise SystemExit("AGENT_CONTRACT.md has no contract block")
    return m.group(0)


def main(argv: list[str]) -> int:
    block = canonical()
    drift = []
    for md in sorted((ROOT / "agents").glob("*.md")):
        text = md.read_text()
        if md.stem == "pipeline-operator":
            continue  # the operator has its own minimal contract
        new = BLOCK.sub(lambda _m: block, text) if BLOCK.search(text) else text.rstrip() + "\n\n" + block + "\n"
        if new != text:
            drift.append(md.name)
            if "--check" not in argv:
                md.write_text(new)
    if "--check" in argv:
        if drift:
            print("contract drift in: " + ", ".join(drift))
            return 1
        print("all agent contracts in sync")
        return 0
    print(f"updated {len(drift)} agent(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
