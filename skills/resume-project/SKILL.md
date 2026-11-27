---
name: resume-project
description: "Resume an interrupted, blocked, budget-exhausted or approval-waiting DevPipeline run from its persisted checkpoint: validates project identity and source revision, detects external changes, invalidates stale evidence, resets interrupted tasks, records the user's answers/approval and relaunches the workflow without redoing completed work."
argument-hint: "[--dir PATH] [--run RUN_ID] [answers / approval note]"
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devpipeline.py *)
---

# /devpipeline:resume-project

User input: $ARGUMENTS

## Steps
1. Project directory = `--dir` or cwd. Check the current state first:
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/devpipeline.py" status --project "<dir>" [--run <RUN_ID>]`
2. If the phase is `AWAITING_APPROVAL`: show the pending questions or plan (from `stop_reason`, `.devpipeline/requirements.json` `blocking_questions`, or the plan in `.devpipeline/tasks.json`). If the user's input does not already answer/approve them, ask (AskUserQuestion). Compose one approval note, e.g. `Q-1: EUR; Q-2: no SSO; plan approved`.
3. If the phase is terminal (`COMPLETED`, `CANCELLED`, `FAILED`), do not resume. If it FAILED before development started but planning finished, suggest `/devpipeline:build-project --dir <dir> --reuse-plan` (keeps the validated plan); otherwise suggest a new improve/repair run.
4. For BLOCKED / NEEDS_HUMAN_REVIEW / BUDGET_EXHAUSTED: show `stop_reason`; resuming only helps if the blocker was resolved (credentials provided, environment fixed, limits raised in `.devpipeline/config.json` `overrides`). Ask the user to confirm the blocker is resolved.
5. **Validate and prepare the resume**:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/devpipeline.py" resume --project "<dir>" [--run <RUN_ID>] [--approve-note "<note>"]
   ```
   It checks project identity, detects external changes to the integration worktree, resets interrupted RUNNING tasks to PENDING, marks stale reports and returns `workflow_script` + `workflow_args`. If `ok` is false, show `error`/`problems` and stop. Report `external_changes` to the user before continuing.
6. **Relaunch** with the Workflow tool: `scriptPath` = `workflow_script`, `args` = `workflow_args`. Completed steps (analysis, planning, finished tasks) are skipped; stale evidence is re-generated.
7. Report the outcome from the run's final-report.md.

## Notes
- Within the same Claude Code session you can alternatively resume a paused/killed workflow run natively from `/workflows`; the DevPipeline resume above also works across sessions because state lives in `.devpipeline/`.
- Raising limits: edit `.devpipeline/config.json` → `"overrides": {"max_agent_calls": 120}` (validated against `config.schema.json`).
- Answers are stored as approvals in the run manifest; the Product Analyst re-runs with them and removes resolved questions.
- Interrupted runs (session closed mid-phase) are first moved to BLOCKED with reason "interrupted", then resumed.
- Evidence produced before an external change is invalidated by fingerprint, never reused.
- Resume never discards task branches; previous attempts' results are kept as `<TASK>.attemptN.json`.
