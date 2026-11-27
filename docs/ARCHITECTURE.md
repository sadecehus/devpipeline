# DevPipeline architecture

## Design in one paragraph
DevPipeline is a Claude Code **plugin** (`devpipeline`) distributed through a local marketplace (`devpipeline-local`, this repository). User-facing **command skills** prepare a run with a deterministic Python CLI and launch a **native dynamic workflow** (`workflows/*.js`). The workflow script owns all control flow — the state machine, dependency-aware waves, retries, repair iterations and budgets — but the workflow runtime cannot touch files or run commands, so every file operation and every deterministic decision is executed by `scripts/devpipeline.py` through a narrow relay agent (`pipeline-operator`, Haiku). The relay's output carries an FNV-1a checksum over canonical JSON that the script verifies, so a mis-transcribed or invented relay is detected and retried. Specialist agents do the work in isolated git worktrees; the deterministic quality gate reads evidence files (exit codes, logs, fingerprints) and alone decides — together with the final reviewer — whether a run may become `COMPLETED`.

```text
 user ──/devpipeline:build-project──▶ command skill (main session)
                                      │ 1. devpipeline.py prepare  → .devpipeline/, run id, config snapshot
                                      │ 2. Workflow tool(scriptPath, args)
                                      ▼
            ┌──────────── workflows/build-project.js (runtime: plain JS, no fs/shell) ────────────┐
            │ state machine · waves · retries · repair loop · agent/operator call budgets          │
            │      │ agent({agentType:'devpipeline:pipeline-operator'}) ──▶ python3 devpipeline.py │
            │      │      context · transition · validate-* · start/finish-tasks · run-checks      │
            │      │      gate · plan-repair · finalize           (checksum-verified JSON relay)   │
            │      │ agent({agentType:'devpipeline:<specialist>', model, effort, schema})          │
            │      ▼                                                                                │
            │  product-analyst → software-architect → [designer] → engineers (parallel waves,    │
            │  one git worktree each) → integration → QA fan-out (parallel) → code review →      │
            │  final review → deterministic gate ──fail──▶ plan-repair → targeted repair tasks ─┐ │
            │                               └─pass──▶ technical writer → final gate → COMPLETED ◀┘ │
            └──────────────────────────────────────────────────────────────────────────────────────┘
   hooks/hooks.json (defense in depth, only for devpipeline:* agents in an active run):
   ownership · coordinator-owned state · unsafe commands · artifact schema validation · event log
```

## Components
| Component | Path | Responsibility |
|---|---|---|
| Plugin manifest / marketplace | `.claude-plugin/plugin.json`, `marketplace.json` | packaging, namespacing (`devpipeline:*`) |
| Agents (20) | `agents/*.md` | specialised system prompts + tool restrictions; common contract synced from `docs/AGENT_CONTRACT.md` |
| Command skills (7) | `skills/{build,improve,audit,repair}-project`, `project-status`, `resume-project`, `project-doctor` | parse TR/EN input, prepare, launch the workflow, report |
| Knowledge skills (17) | `skills/*-engineering`, `testing-standards`, … | substantive guidance preloaded into the relevant agents |
| Workflows (4) | `workflows/*.js` (generated from `scripts/workflow_src/engine.js`) | deterministic orchestration |
| Deterministic core | `scripts/dpcore/` + CLI scripts | schemas, DAG, selection, state, worktrees, checks, gate, issues, reports |
| Schemas (10) | `schemas/*.schema.json` | inter-agent contracts |
| Configuration | `config/*.json` | defaults, profiles, model routing, gates, stacks, agent registry, state machine, integrations |
| Hooks | `hooks/hooks.json` → `scripts/hook_guard.py` | guardrails |
| MCP | `.mcp.json` | Playwright MCP (pinned 0.0.86, headless, isolated), Context7 (remote, no key) |

## Why this split (runtime compatibility)
Verified against Claude Code 2.1.332 and its bundled `workflow-authoring` reference:
- Workflow scripts: `export const meta` (pure literal) first; globals `agent(prompt, {label, phase, schema, model, effort, isolation, agentType})`, `parallel(thunks)` (barrier; failures → `null`), `pipeline()`, `phase()`, `log()`, `args`, `budget`, `workflow()`; no `import()`, no filesystem/shell; `Date.now()`, `Math.random()`, argless `new Date()` throw; ≤16 concurrent agents, ≤1000 per run.
- The Workflow tool (inputs `script | name | scriptPath`, `args`, `resumeFromRunId`) is unavailable inside subagents, so command skills run in the main session (no `context: fork`).
- Plugin agents ignore `permissionMode`, `mcpServers` and `hooks` frontmatter → enforcement is implemented with plugin-level hooks keyed on `agent_type`, and MCP servers are shipped in the plugin `.mcp.json` (workflow agents reach session MCP tools via ToolSearch).
- Verified in the real runtime (tests/smoke/run_runtime_smoke.sh): workflow agents are spawned as `devpipeline:<agent>`, hooks receive `agent_type: "devpipeline:<agent>"` for them (SubagentStart/Stop, PreToolUse), and `${CLAUDE_PLUGIN_ROOT}` resolves to this repository because the marketplace is a local folder. Hooks locate the active run through `~/.local/state/devpipeline/active-runs.json` because workflow agents run with the main session's cwd.
- `claude plugin validate .` lists the agents as `devpipeline:<name>` and the workflows as `devpipeline:pipeline-build|improve|audit|repair`, which is the `agentType` naming the engine uses.

Because imports are impossible, the four workflow files are generated from one engine source (`scripts/build_workflows.py`; tests fail on drift). The meta names (`pipeline-*`) differ from the command skills (`build-project`, …) to avoid slash-command collisions; users start runs through the skills, which pass the prepared `args`.

## Guarantees and their mechanism
| Guarantee | Mechanism |
|---|---|
| Deterministic control flow | JS state machine + Python `state_manager` legal-transition table (`config/state-machine.json`) |
| Dependency-aware parallelism | waves of ready tasks (deps COMPLETED) capped by `max_parallel_agents`; deterministic order (resume-friendly) |
| Safe parallel writes | one git worktree + branch per task from the integration HEAD; overlapping ownership between independent tasks is serialised; post-task git diff vs `owned_paths` (authoritative, catches any tool incl. Bash); PreToolUse hook denies Write/Edit outside the task's paths and write-like Bash commands naming the user's checkout (heuristic); the user's checkout status/HEAD is compared at every finish-tasks and at finalize |
| Structured outputs | `schema` on every agent call + Python schema validation of the files agents write; null/invalid/mismatched results fail the task (bounded retries) |
| Evidence-based completion | `quality_gate.py` reads check reports (command, cwd, exit code, timestamps, counts, log), QA reports, verdicts; mandatory = PASS with valid evidence on the current fingerprint |
| Stale evidence invalidation | code fingerprint (git tree of all non-doc files, temp index) for checks/reviews; production fingerprint (minus QA test paths) for QA reports |
| COMPLETED only when true | `state_manager.transition(COMPLETED)` re-verifies the last gate file and current fingerprint itself |
| Bounded execution | max agent calls, max operator calls, max task retries, max repair iterations, no-progress detection by issue fingerprints, optional `budget.spent()` output-token ceiling |
| Persistence & resume | atomic, fcntl-locked JSON writes; `events.jsonl`; checkpoint per transition; `resume` resets interrupted tasks, detects external changes, marks stale reports |
| User work preserved | user checkout never modified (except fast-forward of a project DevPipeline created, when clean); `.devpipeline/` ignored via `.git/info/exclude` in existing repos |

## Isolation modes
- `worktree` (git available): full guarantees above.
- `serial` (existing non-git project): writing tasks run one at a time in the project directory; changed files detected by content snapshots. Reduced isolation — reported in the run.
- `read-only` (audit): detached worktree of HEAD (non-git: in place, installs skipped).

## Trust boundaries and honest limitations
- The relay agent could in principle fabricate output; the checksum makes that detectable, and the persisted state (written only by Python) remains authoritative: the final status and report are derived from files, not from the relay.
- Hooks are defense in depth; if `agent_type` is absent for some tool call, the guard cannot attribute it and allows it (Claude Code permissions still apply).
- Monetary/token budgets are not hard limits; agent-call/operator-call/iteration limits are.
- Exploratory browser QA depends on the Playwright MCP server being reachable from the agent's context; the repeatable Playwright suite executed by `run_checks.py` is the authoritative e2e evidence.
