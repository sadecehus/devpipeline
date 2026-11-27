# Changelog

## 0.1.3 — 2026-11-28
### Fixed
- `undefined is not an object (evaluating 'tasks.filter')`: a workflow started with an older engine received the new delta-only `finish-tasks` output because the plugin is read from its source folder and the helpers changed mid-run. Workflows and helpers now exchange a protocol version on every call; a mismatch stops the run `BLOCKED` before anything changes (resume relaunches the current workflow). Helpers still return full task lists to pre-handshake workflows.
- Agent results with harmless shape mistakes (string artifacts, missing `run_id`/`agent`/`issues`, extra validation keys such as `evidence`) are normalised by the coordinator instead of failing the task; identity conflicts and missing status/summary are still rejected.
### Added
- `prepare --reuse-plan` / `/devpipeline:build-project --reuse-plan`: start a new run from an earlier run's validated requirements, architecture and task plan (skips analyst and architect).

## 0.1.2 — 2026-11-28
### Fixed (root cause of the field report)
- Raw logs showed the relay model rewriting `$.tasks[8].agent` (`technical-writer` → `backend-engineer`) in a 9-entry, highly repetitive task list — identically on all 3 attempts. `finish-tasks` and `verify-merge` now relay only the tasks that changed (`updates`: id, status, attempt); the workflow applies the deltas to the plan it already holds, so static fields never travel through the relay again.
- The third relay attempt uses a different model (Sonnet) with an explicit "do not assume list entries repeat" note, because the same model repeats the same mistake.
- Task prompts include the exact agent-result JSON skeleton (the same run's first attempt failed schema validation: missing `run_id`/`agent`/`issues`, extra `evidence`).

## 0.1.1 — 2026-11-28
### Fixed
- Operator relay hardening after a field report (run FAILED after 3 rejected `finish-tasks` relays):
  - every helper call carries an op key; a relay retry returns the recorded result and never re-executes the operation;
  - helpers print raw UTF-8 (no `\uXXXX` escapes for the relaying model to mangle) and relay payloads omit free text (titles, long errors);
  - after 3 integrity failures the run stops `BLOCKED` (resumable) instead of `FAILED`, and `runs/<run>/ops/relay-failure-*.json` records exactly which fields the relay changed;
  - native Windows Python supported for state locking (msvcrt fallback).

## 0.1.0 — 2026-11-27
Initial release.

### Added
- Claude Code plugin `devpipeline` with local marketplace `devpipeline-local`.
- 20 agents (19 specialists incl. AI Engineer and AI Evaluation Engineer, plus the internal pipeline operator) with a synced common contract.
- 24 skills: 7 command skills and 17 knowledge skills (architecture, frontend, backend, database, AI, LangChain, LangGraph, RAG, mobile, testing, browser testing, security, AI evaluation, performance, DevOps, code review, documentation).
- 4 native dynamic workflows (build, improve, audit, repair) generated from one engine; checksum-verified operator relay.
- Deterministic core: JSON-schema validation (10 schemas, stdlib validator), DAG validation with cycle/ownership-conflict detection, registry-driven agent selection, git-worktree isolation, stack-aware evidence runner, fingerprint-bound quality gate, issue normalisation/routing, bounded repair planning with no-progress detection, locked atomic state with checkpoints and resume, Markdown reports.
- Plugin hooks (ownership, unsafe commands, coordinator state protection, artifact validation/redaction, agent events), scoped to DevPipeline agents in active runs.
- Playwright MCP (pinned) and Context7 shipped; UI UX Pro Max installed from its official marketplace.
- Idempotent install/update/uninstall scripts, doctor, config validator.
- Tests: 80 unit, 20 mocked workflow scenarios, 3 example smoke tests, and a real-runtime smoke script (`tests/smoke/run_runtime_smoke.sh`) that passed against Claude Code 2.1.332.

### Hardened after independent verification
- Check evidence is accepted only from coordinator-recorded `run_checks` reports; QA reports only at their canonical path from the owning agent; agents cannot mark their own checks optional; unparseable test counts are NOT_RUN.
- Typed approvals (questions vs plan); plan approval re-checked on every resume.
- Pending/interrupted repair tasks are rescheduled on resume; failed repair attempts are superseded by later iterations; gates failing without a repairable issue stop as no-progress.
- An active ai-engineer (or AI code detected in the repository) always activates AI evaluation.
- The user's checkout is compared at every task completion and at finalize; write-like Bash commands naming the checkout are denied.
- A change to the user's checkout stops the run immediately (NEEDS_HUMAN_REVIEW, no futile repair); dirty user files are content-hashed; mock/LLM-judged results are never positive evidence; missing-command issues point at the stack's real manifest; untracked local `.env` files never make a worktree dirty.
- `max_runtime_minutes` enforced at transitions (BUDGET_EXHAUSTED); stored requests are redacted; `.env` files are never committed to task branches; orchestrator gives recovery advice on stops; QA fan-out respects `max_parallel_agents`; final report gained implemented-features, integration and known-limitations sections.

### Known limitations
- Monetary/token budgets are not hard limits (agent/operator/iteration limits are).
- Fallback mode (no Workflow tool) is model-driven orchestration over the same deterministic helpers; a Claude Agent SDK adapter is not included in this version.
- Hooks rely on `agent_type` in hook input; tool calls without it cannot be attributed and are not blocked by the guard (Claude Code permissions still apply).
- The real-runtime smoke verifies plumbing (workflow runtime, relays incl. large outputs, agent resolution, hooks); a complete real build was not executed during setup to avoid large usage.
- Exploratory browser QA requires the Playwright MCP server to be reachable from the subagent; the repeatable Playwright suite remains the authoritative e2e evidence.
