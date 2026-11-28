# Configuration

Resolution order (later wins): `config/defaults.json` → `config/profiles/<profile>.json` → project `.devpipeline/config.json → overrides` → command-line overrides (`prepare --overrides-json`). The result is validated against `schemas/config.schema.json` and snapshotted into each run manifest (a run never changes configuration mid-way).

## Framework files
| File | Purpose |
|---|---|
| `config/defaults.json` | limits, approval/assumption policy, check timeout, live AI evaluation policy, viewports |
| `config/profiles/{fast,balanced,strict}.json` | profile deltas (agents, gates, limits) |
| `config/model-routing.json` | model alias + effort per agent category, per profile |
| `config/quality-gates.json` | gate definitions: source of evidence, applicability rules, default level |
| `config/agent-registry.json` | agents, roles, activation rules, locks, issue routing, fallbacks |
| `config/stack-registry.json` | stack detection, script names, install commands, allowed executables |
| `config/state-machine.json` | legal phase transitions |
| `config/integrations.json` | integration registry |

## Project file `.devpipeline/config.json` (schema `project.schema.json`)
```json
{
  "schema_version": 1,
  "project_id": "generated-uuid",
  "project_name": "doc-qa",
  "created_by_devpipeline": true,
  "profile": "balanced",
  "language": "tr",
  "overrides": {
    "max_parallel_agents": 4,
    "max_repair_iterations": 3,
    "max_output_tokens": 2000000,
    "model_routing": {"backend-engineer": "opus"},
    "gates": {"integration_tests": "mandatory"},
    "agents": {"devops-engineer": "required"},
    "live_ai_evaluation": "mandatory"
  },
  "protected_paths": ["infra/production/"],
  "qa_write_paths": ["tests/", "e2e/", "evals/", "*.test.*", "**/*.test.*", "**/*.spec.*", "playwright.config.*"],
  "approved_environments": ["http://localhost", "http://127.0.0.1"]
}
```
Validate with `python3 scripts/validate_config.py --project <dir>`.

## Key settings
| Key | Meaning |
|---|---|
| `max_parallel_agents` (1–16) | max concurrently writing tasks per wave (QA fan-out also bounded by Claude Code's own cap) |
| `max_agent_calls` | hard cap on specialist agent calls per run → BUDGET_EXHAUSTED |
| `max_operator_calls` | hard cap on relay calls |
| `max_repair_iterations` | correction rounds after initial verification |
| `max_task_retries` | extra attempts for a failed task (invalid/missing result, ownership violation) |
| `max_no_progress_iterations` | consecutive iterations an issue fingerprint may survive before NEEDS_HUMAN_REVIEW |
| `max_check_wait_minutes` | total time the workflow waits for one check run (detached job, waited in ≤ 9 min slices) |
| `max_output_tokens` | optional ceiling checked against `budget.spent()` (output tokens of the turn) — a soft guard, not billing |
| `require_plan_approval` | stop at AWAITING_APPROVAL after planning (strict: true) |
| `auto_accept_assumptions` | accept blocking questions that have `default_if_unanswered` |
| `block_on_preexisting_failures` | reserved; pre-existing failures already prevent COMPLETED (they end as NEEDS_HUMAN_REVIEW) |
| `live_ai_evaluation` | `optional` (NOT_RUN reported) or `mandatory` (NOT_RUN → BLOCKED) |
| `gates.<id>` | `mandatory` / `optional` / `disabled` — a requirement AC whose verification method maps to a gate forces it mandatory |
| `agents.<name>` | `required` / `disabled` — locked agents cannot be disabled |

Limits and routing (`max_*`, `check_timeout_seconds`, `live_ai_evaluation`, `auto_accept_assumptions`, `model_routing`, `effort_routing`)
are **re-read on every resume**, so raising `max_agent_calls` here and running `/devpipeline:resume-project` continues a
BUDGET_EXHAUSTED run. Gate levels, agents and the profile stay as planned for the run.

## Declaring check commands
The check runner discovers commands from real files, never invents them:
- **Node**: `package.json` scripts — `lint`, `typecheck`, `build`, `test`/`test:unit`, `test:integration`, `test:api`, `test:contract`, `test:e2e`, `test:a11y`.
- **Python**: ruff/mypy/pytest when available; pytest suites in `tests/integration`, `tests/api`, `tests/contract`, `tests/e2e`, `e2e`,
  `tests/accessibility`; and explicit declarations in `pyproject.toml` (a file the project's engineers own, so repair tasks can add them):
  ```toml
  [tool.devpipeline.checks]
  e2e = "python -m pytest -q tests/e2e"
  a11y-smoke = { kind = "accessibility", command = "python -m pytest -q tests/ui", timeout_seconds = 600 }
  ```
- **Architecture**: `.devpipeline/architecture.json` `checks[]` (Software Architect). Agents that need one there file a
  `contract_change_requests` entry; it becomes an issue for the architect.

A declared check replaces a discovered one with the same command, or with the same single-purpose tool, kind and directory
(e.g. a declared `pip-audit -r requirements.txt` replaces the generic Python dependency audit). The Python dependency audit
always has a scope (`-r requirements.txt` or the project in `pyproject.toml`); without one it is NOT_RUN instead of scanning
the whole interpreter. Commands may not contain shell metacharacters (`;&|<>$` and backslashes): use forward slashes.

## Profiles
- **fast** — minimal agents (no code reviewer, no performance), essential build/test, secret scan always on; lint/typecheck optional; never claims production readiness.
- **balanced** (default) — full relevant team, security review, browser QA for web, AI evaluation for AI, code review, final review, 5 repairs.
- **strict** — opus planning/review, plan approval, performance + accessibility + dependency audit mandatory, mandatory live AI evaluation, larger budgets.

## Environment variables
| Variable | Effect |
|---|---|
| `DEVPIPELINE_OFFLINE=1` | network checks (dependency audits) report NOT_RUN instead of contacting registries |
| `CLAUDE_BIN` | alternative `claude` executable for install/doctor (tests) |
| `DEVPIPELINE_STATE_DIR` | install log location (default `~/.local/state/devpipeline`) |
| `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` | Claude Code's own workflow concurrency cap |

Checks run with credential-like variables (`*_API_KEY`, `*TOKEN*`, `*SECRET*`, `AWS_*`, …) removed from the environment so test suites cannot accidentally call paid APIs; live AI evaluations are run by the AI Evaluation Engineer only when allowed.
