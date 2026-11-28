---
name: audit-project
description: "Read-only audit of an existing project with DevPipeline QA agents: deterministic checks (build, lint, typecheck, tests, secret scan, dependency audit), code QA, security review, browser QA for web apps, AI evaluation for AI apps, performance (strict) and independent code review. Produces findings that /devpipeline:repair-project can fix after approval."
argument-hint: "[--dir PATH] [--profile fast|balanced|strict] [focus notes]"
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devpipeline.py *)
---

# /devpipeline:audit-project

Runs workflow `devpipeline:pipeline-audit` (`workflows/audit-project.js`).

User input: $ARGUMENTS

## Activation criteria
The user asks for an audit, review, health check or security/quality assessment of an existing project without changing it.

## Steps
1. Project directory = `--dir` or the current working directory. Focus notes (e.g. "özellikle güvenlik") become the request text.
2. **Prepare**:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/devpipeline.py" prepare --project "<dir>" --mode audit --profile <profile> \
     --request-file - <<'DEVPIPELINE_REQUEST'
   Audit request: <focus notes or "full audit">
   DEVPIPELINE_REQUEST
   ```
3. **Launch** the Workflow tool with `scriptPath` = `workflow_script`, `args` = `workflow_args`.
4. **Report** in the user's language from final-report.md: gates, findings grouped by severity with issue ids, scanner availability (an unavailable scanner is NOT_RUN, never a pass), and the command to repair approved findings:
   `/devpipeline:repair-project --from <run_id> ISSUE-001 ISSUE-004`

## Read-only guarantees
- Checks run in a detached DevPipeline-owned worktree of `HEAD` (dependency installs never touch the user's checkout); non-git directories are audited in place with installs skipped.
- QA/reviewer agents are told the run is read-only and the hook guard denies writes outside `.devpipeline/runs/<run>/` during audit runs.
- An audit ends `COMPLETED` only when no blocking finding exists; otherwise `NEEDS_HUMAN_REVIEW` with the findings.

**`workflow_script` is a copy inside the project** (`.devpipeline/runs/<run>/workflow/`), so the Workflow tool can load it whenever the project is inside this session's directories. If it still rejects `scriptPath` ("must be a script path this tool returned, or a file you can already read" — the project is outside the session), copy that file unchanged into the session scratchpad and pass the copy as `scriptPath`, or pass its full contents via the Workflow `script` input. The script is self-contained; `args.plugin_root` still points the helpers at the plugin.
