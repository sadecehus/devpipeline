"""DevPipeline deterministic core (stdlib only).

Everything that must be deterministic — validation, scheduling inputs, state
persistence, quality gates, issue routing — lives here and in the CLI scripts
next to this package. Workflow scripts (workflows/*.js) only hold control flow
and reach this code through the `pipeline-operator` agent.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
SCHEMAS = ROOT / "schemas"
CONFIG = ROOT / "config"
TEMPLATES = ROOT / "templates"


def framework_version() -> str:
    try:
        return (ROOT / "VERSION").read_text().strip()
    except OSError:
        return "0.0.0"
