---
name: improve-project
description: "Improve an existing repository with the DevPipeline team: inspects git status, architecture, manifests, tests and CLAUDE.md, records a baseline (pre-existing failures), plans a scoped change and verifies it with independent QA, security review and deterministic quality gates."
argument-hint: "<change request> [--dir PATH] [--profile fast|balanced|strict] [--max-iterations N] [--concurrency N]"
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devpipeline.py *), Bash(git status *), Bash(git -C * status *)
---

# /devpipeline:improve-project

Runs workflow `devpipeline:pipeline-improve` (`workflows/improve-project.js`) on an **existing** project.

User input: $ARGUMENTS

## Activation criteria
The user wants features added, behaviour changed or a refactor done in an existing codebase (default directory: the current working directory).

## Steps
1. **Parse** the change request and options (same option names as `/devpipeline:build-project`). Project directory = `--dir` or the current working directory.
2. **Inspect before changing anything** (read-only): `git status --short` (report uncommitted changes — they are preserved and are NOT visible to DevPipeline tasks, which branch from HEAD), the README/CLAUDE.md, manifests and test setup. If the directory has no code, suggest `/devpipeline:build-project`. If uncommitted changes exist, tell the user and recommend committing first; continue only if they agree (ask once).
3. **Prepare**:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/devpipeline.py" prepare --project "<dir>" --mode improve \
     --profile <profile> --overrides-json '<json or {}>' --request-file - <<'DEVPIPELINE_REQUEST'
   <change request verbatim>
   DEVPIPELINE_REQUEST
   ```
   Stop and show `error` if `ok` is false.
4. **Launch** the Workflow tool with `scriptPath` = `workflow_script` and `args` = `workflow_args` (JSON object).
5. **Report** from final-report.md in the user's language. Explicitly separate: introduced regressions vs. pre-existing failures (baseline), verified vs. unverified work, and where the change lives: an integration branch `devpipeline/<run>/integration` that the user reviews and merges (`merge_hint`). DevPipeline never merges into the user's branch on its own.

## Guarantees in improve mode
- Never scaffolds over the project or replaces its stack; the architect must keep existing conventions.
- All work happens in DevPipeline-owned git worktrees; the user's checkout, index and uncommitted changes are untouched.
- A baseline check run distinguishes pre-existing failures; they are reported, never claimed as fixed unless they actually pass.
- Non-git projects run with serialised tasks and content-snapshot change detection (reduced isolation; reported in the run).

**`workflow_script` is a copy inside the project** (`.devpipeline/runs/<run>/workflow/`), so the Workflow tool can load it whenever the project is inside this session's directories. If it still rejects `scriptPath` ("must be a script path this tool returned, or a file you can already read" — the project is outside the session), copy that file unchanged into the session scratchpad and pass the copy as `scriptPath`, or pass its full contents via the Workflow `script` input. The script is self-contained; `args.plugin_root` still points the helpers at the plugin.

## Fallback
Without the Workflow tool, follow `${CLAUDE_PLUGIN_ROOT}/docs/WORKFLOWS.md` § "Fallback mode" and state the reduced guarantees.
