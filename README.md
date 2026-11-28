# DevPipeline

DevPipeline is a Claude Code plugin that turns a natural-language project idea (Turkish or English) into a working, tested and documented project through a coordinated team of specialised agents. A native **dynamic workflow** holds the plan in code — state machine, dependency-aware parallel waves in git worktrees, bounded repair loops — while a deterministic Python core validates every contract and a **quality gate that reads real evidence** decides whether a run may be called `COMPLETED`.

## Architecture
```text
/devpipeline:build-project ─▶ command skill ─▶ devpipeline.py prepare ─▶ Workflow: workflows/build-project.js
   ANALYZING   product-analyst ─────────────▶ requirements.json + PRD        (validated, bounded retries)
   PLANNING    software-architect ──────────▶ architecture.json, contracts, tasks.json DAG, agent selection
   DEVELOPING  waves of ready tasks ─────────▶ one git worktree per task, ownership enforced, merged to integration
   TESTING     run_checks (real exit codes) + parallel QA: code-qa · browser-qa · security · ai-eval · performance
   REVIEWING   code-reviewer → final-reviewer → deterministic quality gate
   REPAIRING   issues → routed repair tasks → re-integrate → re-verify   (≤ 5 rounds, no-progress stop)
   DOCUMENTING technical-writer → final gate → COMPLETED | NEEDS_HUMAN_REVIEW | BLOCKED | BUDGET_EXHAUSTED
```
Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/WORKFLOWS.md](docs/WORKFLOWS.md).

## Requirements
- Claude Code ≥ 2.1.269 with dynamic workflows available (paid plan / API; on Pro enable it in `/config`)
- Python ≥ 3.9 (stdlib only), git, Node.js ≥ 20 + npx (Playwright MCP)
- Optional, used when present: Docker, uv, pnpm, ruff, pytest, semgrep, gitleaks, pip-audit, trivy, osv-scanner, k6

## Installation
```bash
cd ~/Desktop/development/devpipeline
scripts/install.sh                 # local marketplace + plugin + UI UX Pro Max (official marketplace)
#   --without-ui-ux-pro-max  --scope user|project|local  --dry-run
```
Then restart Claude Code (or `/reload-plugins`) and run `/devpipeline:project-doctor`.

**Dynamic workflows must be enabled for your account.** If `/devpipeline:build-project` reports that the Workflow tool is unavailable, turn on "Dynamic workflows" in `/config` (or `scripts/install.sh --enable-workflows`, which merges `"enableWorkflows": true` into `~/.claude/settings.json` after a backup). Without it DevPipeline runs only in the documented fallback mode. `tests/smoke/run_runtime_smoke.sh` verifies the real-runtime plumbing (Workflow runtime, operator relay + checksum, agent resolution, hook attribution, large relays) at very low cost; it does not run a full build.

Because the marketplace is a local folder, Claude Code reads the plugin directly from this repository: edits take effect at the next session start or `/reload-plugins` (`scripts/update.sh` re-validates and refreshes).
The installer is idempotent, backs up `~/.claude/settings.json`, changes configuration only through `claude plugin …` commands and logs every change to `~/.local/state/devpipeline/install-log.jsonl`.
Session-only use without installing: `claude --plugin-dir ~/Desktop/development/devpipeline`.

## Quick start
```text
/devpipeline:build-project Build a full-stack AI-powered document assistant using Next.js, FastAPI, PostgreSQL, pgvector and LangGraph. Users should be able to upload PDF documents and ask questions with source citations. Use a balanced profile. --dir ./doc-assistant
```
Claude Code asks you to approve the workflow run; follow progress in `/workflows`; read the result in `.devpipeline/runs/<run>/final-report.md` or with `/devpipeline:project-status`.

## Commands
| Command | Purpose |
|---|---|
| `/devpipeline:build-project <idea> [--dir] [--profile] [--frontend/--backend/--database/--ai-framework] [--max-iterations] [--concurrency]` | new project |
| `/devpipeline:improve-project <change> [--dir]` | change an existing repository (baseline-aware, never scaffolds over it) |
| `/devpipeline:audit-project [--dir]` | read-only audit |
| `/devpipeline:repair-project [--from RUN] ISSUE-…` | repair user-approved findings |
| `/devpipeline:project-status` | phase, tasks, gates, blocking issues, counters |
| `/devpipeline:resume-project [answers]` | continue interrupted / blocked / approval runs |
| `/devpipeline:project-doctor [--mcp]` | environment diagnostics |

## Execution profiles
| | fast | balanced (default) | strict |
|---|---|---|---|
| Team | minimal (no code reviewer/perf) | full relevant team | full + performance |
| Gates | build/tests/secret scan essential | all relevant mandatory | + accessibility, dependency audit, performance |
| Live AI evaluation | optional | optional (NOT_RUN reported) | mandatory (else BLOCKED) |
| Plan approval | no | no | yes |
| Models | sonnet | opus for architecture/reviews | opus for planning too |
| Limits | 2 parallel · 40 agent calls · 3 repairs | 3 · 80 · 5 | 3 · 120 · 5 |
A fast-profile result is a prototype; DevPipeline never presents it as production-ready.

## Agents
Product Analyst, Software Architect, Orchestrator (advisory), UI/UX Designer (uses UI UX Pro Max), Frontend, Backend, Database, **AI Engineer**, Mobile, DevOps, Integration, Browser QA (Playwright MCP), Code QA, Security, **AI Evaluation**, Performance, Code Reviewer, Final Reviewer, Technical Writer, plus the internal Pipeline Operator. Activation is dynamic and recorded per run (an AI project always gets AI evaluation; a backend-only API gets no frontend/browser agents). See [docs/AGENTS.md](docs/AGENTS.md).

## MCP integrations
Shipped: Playwright MCP (pinned `@playwright/mcp@0.0.86`, headless, isolated) and Context7 (remote, no key). Opt-in: Chrome DevTools MCP. Never enabled automatically: GitHub, Figma, database MCPs. See [docs/MCP.md](docs/MCP.md).

## Configuration example
`.devpipeline/config.json` in a project:
```json
{"schema_version": 1, "project_id": "…", "project_name": "doc-assistant", "profile": "balanced",
 "overrides": {"max_parallel_agents": 4, "max_repair_iterations": 3, "gates": {"integration_tests": "mandatory"}}}
```
See [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Repair loop
After the initial implementation and verification, every blocking failure becomes a normalised, fingerprinted, de-duplicated issue routed to the responsible engineer (visual → designer, UI → frontend, API → backend, migration → database, RAG/routing → AI engineer, infra → DevOps, contract → architect, docs → writer, security → owner of the code). Independent repair tasks run in parallel worktrees, are integrated, and the **whole** verification re-runs on the new revision (evidence is fingerprint-bound). It stops on success, after `max_repair_iterations`, when the same issues survive `max_no_progress_iterations` rounds, on budget exhaustion, or when a mandatory gate is BLOCKED (e.g. missing credentials) — returning NEEDS_HUMAN_REVIEW / BLOCKED / BUDGET_EXHAUSTED with actionable reasons. Tests are never weakened and requirements never removed to pass.

## Quality gates
Statuses PASS · FAIL · SKIPPED · NOT_RUN · BLOCKED · ERROR · NOT_APPLICABLE. A mandatory gate passes only with PASS and valid evidence (command, cwd, exit code, timestamps, counts, log) for the current code fingerprint; NOT_APPLICABLE comes from rules, not agents; skipped tests and zero-test suites never pass; unavailable scanners are NOT_RUN; an LLM approval never overrides a failed check. Gates: dependency validation, task completion, requirements coverage (final-review acceptance matrix), install, build, typecheck, lint, unit/integration/API/e2e tests, browser QA, accessibility, secret scan, dependency audit, security review, AI evaluation, performance, code QA, code review, documentation, open blocking issues, final review.

## State storage
```text
<project>/.devpipeline/            (git-ignored; .git/info/exclude for existing repos)
  config.json  state.json  requirements.json  PRD.md  architecture.json  tasks.json  contracts/  adr/
  runs/<run>/manifest.json events.jsonl tasks.json agent-results/ test-reports/ reviews/ issues/ gates/ logs/ screenshots/ artifacts/ final-report.md
  checkpoints/<run>/NNN-<transition>.json     worktrees/<run>/{integration,TASK-…}
```
Writes are atomic and lock-protected; only the coordinator scripts write shared state.

## Security model
Least-privilege tools per agent (no agent can spawn agents; reviewers cannot edit); plugin hooks (active only for `devpipeline:*` agents in an active run) deny writes outside a task's owned paths or into coordinator state, and block pushes, force resets, global installs, deployments, publishing, cloud changes, destructive DB statements and permission bypasses; artifacts are schema-validated and secret-redacted on write; check commands run with credential variables stripped. DevPipeline never enables bypassPermissions, never pushes, deploys, publishes or creates paid resources, and never modifies production databases. Prompt-level rules are not treated as a security boundary — tool restrictions, hooks, worktree isolation and post-hoc git verification are.

## Cost considerations
The plugin adds roughly 4.8k tokens of skill/agent descriptions to every session while enabled (`claude plugin details devpipeline`); disable it with `claude plugin disable devpipeline@devpipeline-local` when not in use. Each run uses one call per analyst/architect/task/QA agent/reviewer plus cheap Haiku relay calls; each repair round re-runs QA and reviews. Token/cost usage is not observable from the workflow runtime and is never estimated in reports; agent/operator call counts are exact. Use `fast` for experiments, lower `max_agent_calls` to cap usage.

## Development of the framework
```bash
python3 -m unittest discover -s tests/unit        # 89 deterministic unit tests
node --test tests/integration/                    # 26 mocked end-to-end workflow scenarios (real helpers, fake agents)
python3 -m unittest discover -s tests/smoke       # example projects: activation + gates
python3 scripts/validate_config.py && claude plugin validate .
tests/smoke/run_runtime_smoke.sh                  # real Claude Code runtime (small usage): relay, checksum, agentType, hooks
```
Edit `scripts/workflow_src/engine.js` then `python3 scripts/build_workflows.py`; edit `docs/AGENT_CONTRACT.md` then `python3 scripts/sync_agent_contract.py`. Extension guide: [docs/EXTENDING.md](docs/EXTENDING.md).

## Troubleshooting
See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) and the incident log [docs/INCIDENTS.md](docs/INCIDENTS.md); start with `/devpipeline:project-doctor`.

## Update and uninstall
```bash
scripts/update.sh        # validate + tests + refresh marketplace + claude plugin update
scripts/uninstall.sh     # removes plugin + local marketplace only; --remove-ui-ux-pro-max (only if DevPipeline installed it); --keep-data
```
Project `.devpipeline/` directories and `devpipeline/*` branches are never deleted by the uninstaller.

## Privacy
Nothing is uploaded by DevPipeline itself. Shipped integrations contact `mcp.context7.com` (documentation lookups) and the npm registry (to fetch Playwright MCP). Observability integrations (LangSmith, Langfuse, OpenTelemetry) in generated projects are opt-in.

## License
MIT
