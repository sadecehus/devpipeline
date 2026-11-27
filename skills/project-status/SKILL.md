---
name: project-status
description: "Show the DevPipeline status of a project: current phase, completed/pending/failed tasks, selected agents, repair iteration, open blocking issues, quality-gate results and exact agent/operator call counters (token/cost usage is not observable and is never estimated)."
argument-hint: "[--dir PATH] [--run RUN_ID]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devpipeline.py *), Bash(tail *)
---

# /devpipeline:project-status

User input: $ARGUMENTS

## Activation criteria
The user asks what DevPipeline is doing / did for a project ("durum", "status", "hangi aşamada", "which gates failed").

## Steps
1. Project directory = `--dir` or the current working directory; optional `--run`.
2. Run:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/devpipeline.py" status --project "<dir>" [--run <RUN_ID>]
   ```
3. If `ok` is false (not a DevPipeline project), say so and suggest `/devpipeline:build-project` or `/devpipeline:audit-project`.
4. Present, in the user's language, a compact summary:
   - run id, mode, profile, phase (and `stop_reason` if stopped);
   - tasks grouped by status (`tasks` map), selected agents;
   - repair iteration and counters (`agent_calls`, `operator_calls`) — say explicitly that token/cost figures are not available;
   - quality gate: list gates that are not PASS/NOT_APPLICABLE;
   - open blocking issues (id, severity, assigned agent, title).
5. Active agents are observable in `/workflows` while a run is in progress; mention it if the phase is not terminal. For recent activity you may show the last events: `tail -n 15 "<dir>/.devpipeline/runs/<run>/events.jsonl"`.
6. Point to the final report path (`report`) when it exists.

## Notes
- Read-only: never modifies state.
- Phases: INITIALIZING, ANALYZING, PLANNING, AWAITING_APPROVAL, DESIGNING, DEVELOPING, INTEGRATING, TESTING, REVIEWING, REPAIRING, DOCUMENTING, COMPLETED, BLOCKED, NEEDS_HUMAN_REVIEW, BUDGET_EXHAUSTED, CANCELLED, FAILED.
- `COMPLETED` is only reachable when the deterministic gate passed for the current code fingerprint.
- Statuses of individual gates: PASS, FAIL, SKIPPED, NOT_RUN, BLOCKED, ERROR, NOT_APPLICABLE — only PASS (with evidence) and validated NOT_APPLICABLE count as passing.
- Status output is produced from persisted files (`.devpipeline/runs/<run>/manifest.json`, `gates/`, `issues/`), never from memory.
- For a human-readable full picture open `final-report.md` of the run.
- If several runs exist, the latest (`current_run_id` in `.devpipeline/state.json`) is shown unless `--run` is given.
- Use `/devpipeline:resume-project` to continue BLOCKED / NEEDS_HUMAN_REVIEW / BUDGET_EXHAUSTED / AWAITING_APPROVAL runs.
- Use `/devpipeline:project-doctor` when the status command itself fails.
