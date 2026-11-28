# Incident log

Every real-world failure of DevPipeline itself (not of the projects it builds): what was observed,
the root cause, how it was diagnosed, the fix, and the regression test that keeps it fixed.
Add a new entry for every new field problem **before** fixing it, and keep entries short.

Diagnostics to collect first (all local, nothing uploaded):

| Question | Where to look |
|---|---|
| What happened, in order? | `.devpipeline/runs/<run>/events.jsonl` (filter out `AGENT_STARTED/STOPPED`) |
| Why did a task fail? | `runs/<run>/tasks.json` → `failure_reason`; `agent-results/<TASK>.json` (+ `.attemptN.json`) |
| Why did a gate fail? | latest `runs/<run>/gates/gate-NNN.json` → `failing_mandatory`, per-gate `reason`; `issues/ISSUE-*.json` |
| Did a relay corrupt or lose a helper result? | `runs/<run>/ops/relay-failure-*.json` (true output vs relayed, field diff) |
| What did a helper really print? | `runs/<run>/ops/<invocation>-<seq>-<command>.json` (op cache) |
| How long did checks take, which one hung? | `runs/<run>/test-reports/checks-*.json` (`duration_ms`), `logs/<label>--<check>.log`, `ops/jobs/<job>.log` |
| Which DevPipeline version ran? | `runs/<run>/manifest.json` → `framework_version`; protocol errors in events |

Useful one-liner (events without agent noise):
```bash
python3 -c "import json,sys;[print(e['ts'][11:19],e['event'],e.get('task_id',''),(e.get('detail') or '')[:120]) for e in map(json.loads,open(sys.argv[1])) if e['event'] not in ('AGENT_STARTED','AGENT_STOPPED','CHECKPOINT_CREATED')]" .devpipeline/runs/<run>/events.jsonl
```

---

## INC-001 — Relay mis-copied a long, repetitive task list (0.1.1 → 0.1.2)
- **Observed:** BIST bot run ended `FAILED` after `finish-tasks` "failed integrity check" 3×; `TASK_COMPLETED` logged 3 times for one task.
- **Root cause:** the Haiku relay rewrote `$.tasks[8].agent` (`technical-writer` → `backend-engineer`) in a 9-entry list where 8 entries were `backend-engineer` — the same mistake on every retry. Retries also re-executed the (state-changing) helper.
- **Diagnosis:** recomputed checksums from the workflow journal; diffed raw helper stdout vs relayed object.
- **Fix:** op keys make helpers idempotent (retry returns the recorded result); `finish-tasks`/`verify-merge` relay only deltas (`updates`); third attempt uses another model; raw UTF-8 output; 3 failures → `BLOCKED` (resumable) + `ops/relay-failure-*.json`.
- **Tests:** integration 21, 22; unit `test_op_key_makes_operations_idempotent`.
- **Lesson:** never relay data the workflow already has; assume an LLM relay repeats patterns.

## INC-002 — Agent results rejected for harmless shape mistakes (0.1.2 → 0.1.5)
- **Observed:** correct work discarded: missing `run_id`/`agent`/`issues`, extra `evidence`, artifacts as strings, `evidence_path: null`, free-text `issues`.
- **Root cause:** strict schema validation of agent-written files with no coordinator-side normalisation.
- **Fix:** `normalize_result()` fills identity fields the coordinator knows, converts artifacts/issues, drops nulls; identity conflicts and missing status/summary still fail. Task prompts include the exact JSON skeleton.
- **Tests:** integration 23; unit `test_result_normalisation_keeps_real_errors`, `test_normalisation_null_evidence_and_string_issues`.

## INC-003 — `undefined is not an object (evaluating 'tasks.filter')` (0.1.3)
- **Observed:** istanbul-reels run crashed after planning, all 16 tasks PENDING.
- **Root cause:** version skew. The plugin is read **from its source folder**; the helpers were updated while a workflow started with the old engine was running, and the new `finish-tasks` output (deltas) lacked the `tasks` list the old engine expected.
- **Fix:** protocol handshake (`PROTOCOL` in `devpipeline.py` = `PROTOCOL` in `engine.js`, sent on every call; mismatch → `BLOCKED`, nothing changed); legacy engines still get full lists; `prepare --reuse-plan` to keep a validated plan after a framework failure.
- **Tests:** integration 24; unit `test_protocol_handshake`.
- **Rule:** do not edit the DevPipeline folder while runs are active; bump `PROTOCOL` whenever the helper↔workflow JSON contract changes.

## INC-004 — Hook guard blocked `finalize`; run stuck without report (0.1.4)
- **Observed:** run ended BLOCKED, no `final-report.md`, phase left in `DEVELOPING`; relay-failure showed `true_output: {}` and the relayed text "pipeline-operator may only run a single DevPipeline helper script".
- **Root cause:** the stop reason (with orchestrator advice `… | recovery advice: …`) is passed single-quoted; the guard's metacharacter check ignored quoting and saw `|`.
- **Fix:** quote-aware scan (`_outside_single_quotes`, handles `'\''`); real unquoted operators still denied.
- **Tests:** unit `test_operator_quoted_metacharacters_are_literal`.
- **Lesson:** an empty `true_output` in relay-failure means the helper never ran — look at hooks/permissions, not at the relay.

## INC-005 — TASK-013 discarded 3× (lost hours of work) (0.1.4)
- **Observed:** review-screen task FAILED: no result → schema errors → `ownership violation: ['AGENTS.md']` after a 4 h attempt; every retry reset the branch.
- **Root cause:** (a) INC-002 gaps; (b) any out-of-ownership edit failed the whole task; (c) `worktree add -B` overwrote previous attempts.
- **Fix:** out-of-ownership edits are reverted and reported (`OWNERSHIP_REVERTED`); previous attempts preserved on `<TASK>-attemptN` branches and offered to the next attempt; `resume --retry-failed` for a user-approved fresh retry budget.
- **Tests:** integration 25, 26.

## INC-006 — Check run exceeded the relay's 600 s Bash limit (0.1.5)
- **Observed:** resumed run BLOCKED in `TESTING`; relay-failure said run-checks "was moved to background … output never captured".
- **Root cause:** all checks run in one helper call (here 445–572 s, then > 600 s); half of them ran twice because architect-declared checks duplicated discovered ones under other names.
- **Fix:** detached check jobs (`start-checks` + `wait-checks` in ≤ 9 min slices, `max_check_wait_minutes`); dedupe by command + cwd.
- **Tests:** unit `test_long_checks_run_as_detached_job_with_bounded_waits`, `test_declared_check_dedupes_identical_discovered_check`; real-runtime smoke "detached check job survived its starting relay".
- **Rule:** no helper invoked through the relay may run longer than ~9 minutes; long work must be a job.

## INC-007 — Secret scan / dependency audit produced unfixable findings (0.1.5)
- **Observed:** `secret_scan` failed with 76 findings; repair tasks returned PARTIAL; `dependency-audit` failed on dev-only `prisma` advisories.
- **Root cause:** the name=value heuristic flagged test fixtures, code identifiers (`tokenHash = sha256(…)`), placeholder values, cache-key names, and matched across line breaks (`TOKEN=` + next line); audits included dev dependencies.
- **Fix:** heuristic limited to literal values (unquoted only in env/config files), skipped in test/mock/fixture paths, no cross-line matching, identifier-like names and placeholder words ignored; strong key formats still everywhere. Audits use `--omit=dev` / `--prod`.
- **Tests:** unit `test_secret_scan_precision`.
- **Lesson:** a gate the agents cannot satisfy without weakening tests is a framework bug — check precision on the real project before blaming agents.

## INC-008 — `scriptPath` rejected when the plugin is outside the session (open, workaround documented)
- **Observed:** "scriptPath must be a script path this tool returned, or a file you can already read".
- **Root cause:** the Workflow tool only loads scripts from the session's working/added directories; the plugin folder is elsewhere.
- **Workaround (in the command skills):** copy the generated workflow unchanged into the session scratchpad and launch that copy (or pass it via `script`). Works because workflows are self-contained and `args.plugin_root` points to the helpers.

## Earlier hardening (independent review before first field use)
Gate evidence provenance (only coordinator-recorded check reports; QA reports only from the owning agent), unparseable test counts = NOT_RUN, typed approvals, repair-resume, superseded repair tasks, AI evaluation forced by an active ai-engineer, user-checkout change detection, temp-path hook exemption order. See CHANGELOG 0.1.0 "Hardened after independent verification".
