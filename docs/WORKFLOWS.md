# Workflows

| Command (user) | Workflow file | Workflow name | Purpose |
|---|---|---|---|
| `/devpipeline:build-project` | `workflows/build-project.js` | `devpipeline:pipeline-build` | new project from an idea |
| `/devpipeline:improve-project` | `workflows/improve-project.js` | `devpipeline:pipeline-improve` | scoped change to an existing repo (baseline-aware) |
| `/devpipeline:audit-project` | `workflows/audit-project.js` | `devpipeline:pipeline-audit` | read-only audit |
| `/devpipeline:repair-project` | `workflows/repair-project.js` | `devpipeline:pipeline-repair` | repair user-approved findings |
| `/devpipeline:resume-project` | (relaunches the run's workflow) | — | continue interrupted/blocked/approval runs |

All four are generated from `scripts/workflow_src/engine.js` by `python3 scripts/build_workflows.py`. They require `args = {plugin_root, project_dir, run_id, agent_prefix}` produced by `devpipeline.py prepare|resume`; started without args they stop immediately with an explanation.

## Build / improve phases
1. **INITIALIZING** — `context`: loads config, creates the integration worktree (`devpipeline/<run>/integration`), writes `active.json` (enables hooks). Improve/repair: `baseline` check run records pre-existing failures.
2. **ANALYZING** — Product Analyst → `requirements.json`, `PRD.md`; `validate-requirements` (schema, unique ids, measurable ACs) with bounded re-prompts carrying the validator errors. Blocking questions without defaults → **AWAITING_APPROVAL** (run returns; answer via resume).
3. **PLANNING** — Software Architect → `architecture.json`, `tasks.json`, contracts, ADRs; `validate-plan`: schema, DAG (unique ids, known agents, missing deps, cycles), ownership conflicts, requirement coverage, stack-preference compliance, **registry-driven agent selection** (locked agents such as AI evaluation cannot be removed). Optional plan approval (strict profile).
4. **DESIGNING** (if designer tasks) and **DEVELOPING** — waves: `start-tasks` (worktree per task from integration HEAD, RUNNING, active ownership) → `parallel(specialists)` → `finish-tasks` (result file validation, git diff vs owned paths, commit on task branch, merge into integration, retries/BLOCKED propagation). Merge conflicts → Integration Engineer resolves → `verify-merge`.
5. **INTEGRATING** — `integration-status`; Integration Engineer validates manifests/contracts/migrations/build/boot when there were several tasks.
6. **TESTING** — `run-checks` (pre) → QA fan-out in parallel (code QA, browser QA, security, AI evaluation, performance — whichever are selected) against the production fingerprint → commit QA tests (scope-checked) → `run-checks` (post, authoritative).
7. **REVIEWING** — Code Reviewer (balanced/strict) → Final Reviewer (acceptance matrix) → `gate`.
8. **REPAIRING** (bounded) — `plan-repair`: stop conditions (budget, no progress, BLOCKED gates such as unavailable mandatory live evaluation, pre-existing-only failures), then repair tasks grouped by responsible agent (routing table), executed in waves, integrated, and the whole verification re-runs on the new revision (evidence is fingerprint-bound, so stale evidence is never reused).
9. **DOCUMENTING** — once only documentation is failing: Technical Writer (docs scope only) → final gate. Documentation edits do not change the code fingerprint, so test evidence stays valid.
10. **finalize** — COMPLETED (state manager re-verifies the gate), or BLOCKED / NEEDS_HUMAN_REVIEW / BUDGET_EXHAUSTED / FAILED with reason; report generation; worktree cleanup; fast-forward for projects DevPipeline created.

## Audit
INITIALIZING → TESTING → REVIEWING → finalize. Agents selected from detected repository features (`select-audit`); no writers. `COMPLETED` only when no blocking finding exists, otherwise `NEEDS_HUMAN_REVIEW` with findings for `/devpipeline:repair-project`.

## Repair
`import-findings` copies the **user-approved** issues from a source run and creates repair tasks whose `owned_paths` are exactly the issues' affected files; then development → integration → verification → repair loop as above.

## Limits (defaults, `config/defaults.json` / profiles)
| Setting | fast | balanced | strict |
|---|---|---|---|
| max_parallel_agents | 2 | 3 | 3 |
| max_agent_calls | 40 | 80 | 120 |
| max_operator_calls | 200 | 200 | 200 |
| max_repair_iterations | 3 | 5 | 5 |
| max_task_retries | 2 | 2 | 2 |
| max_no_progress_iterations | 2 | 2 | 2 |
"Five repair iterations" means five correction rounds *after* the initial implementation and verification.

## Cost considerations
A balanced run of a small project uses roughly: 1 analyst + 1 architect + one call per task + 1 integration + one call per selected QA agent + 1–2 reviewers + 1 writer, plus ~2× that number of cheap Haiku operator calls. Every repair iteration re-runs QA and reviews because the code changed. Claude Code warns before large runs (> 25 agents); use the `fast` profile for prototypes.

## Resume
- Cross-session: `/devpipeline:resume-project` (state in `.devpipeline/`). Completed steps (analysis, planning, development) and completed tasks are skipped; RUNNING tasks restart as new attempts; reports with stale fingerprints are ignored.
- Same session: the native `/workflows` view can also resume a paused/killed run (`resumeFromRunId`); positional agent-call caching then replays completed calls.

## Fallback mode
Used only when the Workflow tool is unavailable (`disableWorkflows: true`, `CLAUDE_CODE_DISABLE_WORKFLOWS=1`, a plan without dynamic workflows, or Claude Code < 2.1.248). The command skill then drives the **same** deterministic operations itself:

1. `devpipeline.py prepare …` (as usual), then `devpipeline.py context --project P --run R`.
2. For each phase call `devpipeline.py transition --to <PHASE>`; spawn the specialist with the Agent tool using `subagent_type: "devpipeline:<agent>"` and the same prompt fields the engine uses (task id, working directory, result path, fingerprint).
3. After each agent: the corresponding `validate-requirements`, `validate-plan`, `start-tasks`/`finish-tasks`, `run-checks`, `validate-reports`, `gate`, `plan-repair`, `finalize` commands — exactly in the order listed above.

Guarantees in fallback mode: state persistence, worktree isolation, ownership verification, schema validation, the quality gate and the COMPLETED guard are identical (they live in Python). **Not** guaranteed: the sequencing, parallelism, retry counting and budget enforcement are performed by the main-session model following instructions rather than by a deterministic script, so a step can be skipped or reordered by mistake (the gate still prevents a false COMPLETED). A Claude Agent SDK adapter would restore script-level determinism; it is not shipped in this version because the native runtime is available here (see CHANGELOG "Known limitations").
