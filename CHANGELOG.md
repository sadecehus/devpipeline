# Changelog

## 0.1.7 — 2026-11-28
### Fixed (istanbul-reels resume review: blockers no repair could clear)
- Check tools that rewrite tracked files (Next.js `next-env.d.ts` differs between `next dev` and `next build`) made every check report stale (fingerprint taken before the build) and every QA step raised a new blocking "outside allowed paths" issue. The runner now restores what the check tools changed; QA/docs commits revert out-of-scope edits and report them (`OWNERSHIP_REVERTED`) instead of blocking.
- A FAILED repair task no longer blocks `task_completion` once none of its issues is open.
- AI evaluation: deterministic evals executed against a fake model (exit 0 + log) count as evidence even when labelled `mock`; the skill no longer tells agents to label them `mock`. Without this the gate could never pass with `live_ai_evaluation: optional`.
- Security skill/agent: dependency audits cover production dependencies with a scope (`npm audit --omit=dev`, `pip-audit -r requirements.txt`); dev-only advisories are non-blocking issues instead of a failing check in a mandatory gate.

## 0.1.6 — 2026-11-28
### Fixed (bist-bot field report from Windows: run could not reach COMPLETED)
- **Resume re-reads limits** (`max_agent_calls`, other `max_*`, timeouts, routing) from `.devpipeline/config.json` overrides (and new `resume --overrides-json`); prepare-time CLI overrides are kept. A resume that would still be over budget refuses and starts nothing. `max_runtime_minutes` counts from the latest resume.
- **Secret scan ran nowhere on Windows**: its own generated command (`python3 'C:\…\redact_secrets.py' scan .`) tripped the shell-metacharacter filter. It is now a built-in run with the helper's interpreter.
- **Python dependency audit was unscoped** (`pip-audit` without arguments scans the whole interpreter: 224 conda/torch findings). Now `pip-audit -r requirements.txt` or `pip-audit .`; NOT_RUN when there is nothing to scope. A declared check with the same single-purpose tool, kind and directory replaces the discovered one, and the dependency-audit gate counts it.
- **Mandatory integration/e2e/accessibility checks could not be added by any repair**: they could only be declared in architecture.json. Python projects can now declare checks in `pyproject.toml` (`[tool.devpipeline.checks]`), and pytest suites in `tests/integration|api|contract|e2e|accessibility` are discovered. The "no executable check" issue explains the exact options and its repair task owns the manifest/test folder.
- **Contract change requests were rejected and went nowhere**: the prompt never gave their shape. Shape is in the task prompt and agent contract; `title/summary/file/why`-style entries are mapped; requests become issues for the Software Architect (blocking when the task could not finish), whose contract task may now also update `architecture.json` `checks`.
- **PARTIAL repairs lost their work** (TASK-921-R2's ruff fix): a partial repair with changes is merged; the gate re-evaluates every issue.
- **Missing agent results** cost a retry each: a result written relative to the worktree is moved; otherwise the agent's returned status/summary becomes a minimal result (no validation claims; checks still decide). Protocol 3.
- **Error-path validations** ("exits 2 on a bad date") with `expected_exit_code` are a PASS; an unexplained PASS with non-zero exit is downgraded to ERROR instead of failing the task.
- **Gate reasons**: report gates name the failing checks (no more "FAIL: code_qa report PASS"); evidence `log_path` is resolved relative to the run directory/worktree and Git-Bash paths.
- **Windows**: Playwright MCP is started via `node scripts/mcp_npx.mjs` (bare `npx` gives "Connection closed"); `wait-checks` no longer probes with `os.kill(pid, 0)` (terminates the process on Windows); detached check jobs use Windows creation flags; reviewers could not write their verdicts (backslash path check in the hook guard); `%TEMP%` is a scratch root; `python3` falls back to the helper's interpreter.
- **Startup**: `prepare --request-file -` reads stdin (`/dev/stdin` does not exist for Windows Python); `workflow_script` is a copy in `.devpipeline/runs/<run>/workflow/` so the Workflow tool accepts it without copying by hand.

## 0.1.5 — 2026-11-28
### Fixed (istanbul-reels resume ended BLOCKED in run-checks)
- Check runs (≈450–570 s here) no longer have to fit into one relayed Bash call (600 s limit): `start-checks` launches a detached job and `wait-checks` waits in ≤ 9 min slices up to `max_check_wait_minutes` (default 180); a stuck suite ends BLOCKED with the job log path. Verified in the real runtime (a 20 s check outlived the relay that started it).
- Architect-declared checks replace discovered checks with the same command and directory (e2e, integration, accessibility … ran twice).
- Secret scan precision (76 false positives → 0 on the project, real keys still caught): name=value heuristic only for literal values, unquoted only in env/config files, not in test/mock/fixture paths; no matching across line breaks; identifier-like names and placeholder words ignored. Strong key formats (sk-ant-, AKIA, ghp_, private keys …) are still reported everywhere.
- Runtime dependency audit checks production dependencies (`npm audit --omit=dev`, `pnpm audit --prod`); dev-tool advisories no longer block.

## 0.1.4 — 2026-11-28
### Fixed (istanbul-reels run ended BLOCKED without a report)
- `finalize` was denied by the hook guard: the stop reason (with the orchestrator's advice, `… | recovery advice: …`) is passed single-quoted, but the guard flagged the quoted `|`. The guard now scans only text outside single quotes (quote-aware, handles `'\''`).
- TASK-013 was discarded three times for framework reasons, not for its work:
  - results with `evidence_path: null` or free-text `issues` are normalised (strings become non-blocking issues);
  - edits outside owned paths (here `AGENTS.md`) are reverted in the task worktree and reported (`OWNERSHIP_REVERTED`) instead of failing the whole task;
  - every earlier attempt is preserved on `devpipeline/<run>/<TASK>-attemptN` and the next attempt is told to reuse it.
- Counters are persisted on every helper call (the manifest showed `agent_calls: 1` after 68 agents).
### Added
- `resume --retry-failed`: a user-approved fresh, still bounded, retry budget for FAILED/BLOCKED tasks.
- Command skills explain how to launch when `scriptPath` is outside the session; `--reuse-plan` with an empty request reuses the earlier request.

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
