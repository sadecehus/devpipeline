---
name: repair-project
description: "Repair user-approved findings from an earlier DevPipeline audit/build run: shows the findings, requires explicit approval of the exact issue ids, creates scoped repair tasks limited to the affected files, then re-verifies with QA, review and the deterministic gate."
argument-hint: "[--from RUN_ID] [ISSUE-001 ISSUE-002 ...] [--dir PATH]"
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devpipeline.py *), Bash(ls *)
---

# /devpipeline:repair-project

Runs workflow `devpipeline:pipeline-repair` (`workflows/repair-project.js`) on **approved findings only**.

User input: $ARGUMENTS

## Steps
1. Project directory = `--dir` or the current working directory. Source run = `--from RUN_ID`, else the latest run with findings: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/devpipeline.py" status --project "<dir>"` (and list `.devpipeline/runs/`).
2. Read the source run's open findings from `.devpipeline/runs/<run>/issues/ISSUE-*.json` (status OPEN/IN_REPAIR, skip RESOLVED/PRE_EXISTING). Show id, severity, category, title, affected files and assigned engineer.
3. **Get explicit approval.** If the user already listed issue ids, confirm them; otherwise ask (AskUserQuestion, multi-select) which findings to repair. Never repair findings the user did not approve. Findings without `affected_files` cannot be scoped — tell the user.
4. **Prepare**:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/devpipeline.py" prepare --project "<dir>" --mode repair \
     --source-run <RUN_ID> --issues ISSUE-001,ISSUE-004 --request "Repair approved findings ISSUE-001, ISSUE-004"
   ```
5. **Launch** the Workflow tool with `scriptPath` = `workflow_script`, `args` = `workflow_args`.
6. **Report** which findings were resolved (verified by fresh evidence), which remain, and the integration branch to review/merge.

**If the Workflow tool rejects `scriptPath`** ("must be a script path this tool returned, or a file you can already read"), the plugin folder is outside this session's directories. Copy the script unchanged into the session scratchpad (`cp "<workflow_script>" "<scratchpad>/"`) and pass that copy as `scriptPath`, or pass its full contents via the Workflow `script` input. The script is self-contained; `args.plugin_root` still points the helpers at the plugin.

## Rules
- Repair tasks may only touch the approved findings' affected files; the hook guard enforces the ownership.
- Tests must not be weakened and requirements must not be removed to make checks pass.
