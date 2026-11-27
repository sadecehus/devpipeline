# Changelog

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
