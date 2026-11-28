---
name: build-project
description: "Build a new software project from a natural-language idea (Turkish or English) with the DevPipeline multi-agent team: requirements, architecture, parallel implementation in git worktrees, independent QA/security/AI evaluation, deterministic quality gates and bounded repair loops."
argument-hint: "<project idea> [--dir PATH] [--reuse-plan] [--profile fast|balanced|strict] [--frontend X] [--backend X] [--database X] [--ai-framework X] [--max-iterations N] [--concurrency N]"
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/devpipeline.py *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py *)
---

# /devpipeline:build-project

Starts the **real** DevPipeline orchestration (`workflows/build-project.js`, workflow name `devpipeline:pipeline-build`). You (the main session) only prepare the run, launch the workflow and report the result — you never implement the project yourself.

User input: $ARGUMENTS

## Activation criteria
The user typed `/devpipeline:build-project …` or explicitly asked DevPipeline to build a new project. For changes to an existing repository use `/devpipeline:improve-project` instead.

## Steps

1. **Parse the input** (Turkish or English). Extract:
   - the project idea (everything that is not an option);
   - `--dir PATH` → project directory. Default: a kebab-case slug of the idea under the current working directory (e.g. `./document-assistant`). If the directory exists and contains code, stop and suggest `/devpipeline:improve-project`.
   - `--profile` (default `balanced`; words like "hızlı/prototip/fast" → `fast`, "production/strict/güvenlik kritik" → `strict`);
   - stack options (`--frontend`, `--backend`, `--database`, `--ai-framework`) and stack names written in the idea (e.g. "Next.js, FastAPI, PostgreSQL, pgvector, LangGraph");
   - `--max-iterations N` → `max_repair_iterations`, `--concurrency N` → `max_parallel_agents` (1–16).
   - `--reuse-plan` → the directory already holds a validated plan from an earlier run that stopped (`.devpipeline/requirements.json`, `architecture.json`, `tasks.json`): pass `--reuse-plan` to `prepare` (and drop `--new`); the idea text may be omitted (the earlier request is reused). Analyst and architect are skipped when the existing files validate.
   Use defaults for anything not given; do not ask about non-essential preferences.
2. **Compose the request text**: the original idea verbatim, followed by a line `Stack preferences: frontend=…, backend=…, database=…, ai_framework=…` for the stack choices you extracted (omit unknown ones).
3. **Prepare the run** (creates `.devpipeline/`, run id, config snapshot; nothing is generated yet):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/devpipeline.py" prepare --project "<dir>" --mode build --new \
     --profile <profile> --overrides-json '<json with max_repair_iterations / max_parallel_agents if given>' \
     --request-file - <<'DEVPIPELINE_REQUEST'
   <request text>
   DEVPIPELINE_REQUEST
   ```
   The command prints one JSON object. If `ok` is false, show `error` and stop.
4. **Launch the workflow** with the Workflow tool, passing the prepared values exactly:
   - `scriptPath`: the `workflow_script` value from step 3;
   - `args`: the `workflow_args` object from step 3 (as a JSON object, not a string).
   Tell the user in one line what is starting (run id, profile, directory) and that Claude Code will ask them to approve the workflow run. Progress is visible in `/workflows`.
5. **Report the result** when the workflow returns. Read the `report` file (final-report.md) and summarise in the user's language: final status, verified vs. unverified vs. missing requirements, failing gates, remaining blocking issues, where the code is (`integration_branch`, `fast_forwarded`), and next steps. Never call a run COMPLETED unless `final_status` is `COMPLETED`.
   - `AWAITING_APPROVAL`: show the questions/plan from `reason` and tell the user to answer via `/devpipeline:resume-project <answers>`.

**`workflow_script` is a copy inside the project** (`.devpipeline/runs/<run>/workflow/`), so the Workflow tool can load it whenever the project is inside this session's directories. If it still rejects `scriptPath` ("must be a script path this tool returned, or a file you can already read" — the project is outside the session), copy that file unchanged into the session scratchpad and pass the copy as `scriptPath`, or pass its full contents via the Workflow `script` input. The script is self-contained; `args.plugin_root` still points the helpers at the plugin.

## Fallback (Workflow tool unavailable)
If the Workflow tool is missing or disabled ("Dynamic workflows" off in `/config` / `enableWorkflows` unset on this plan, `disableWorkflows`, `CLAUDE_CODE_DISABLE_WORKFLOWS`), tell the user how to enable it (`/config` → Dynamic workflows, or `<devpipeline repo>/scripts/install.sh --enable-workflows`, then restart) and ask whether to continue in fallback mode now. If they choose fallback, say so explicitly and follow `${CLAUDE_PLUGIN_ROOT}/docs/WORKFLOWS.md` § "Fallback mode": drive the same `devpipeline.py` operations yourself and delegate each task to the matching `devpipeline:<agent>` subagent with the Agent tool. State clearly that fallback mode has weaker guarantees (orchestration is model-driven, not a deterministic script; the quality gate and state manager remain deterministic).

## Rules
- Do not write application code yourself; do not edit `.devpipeline/` files by hand.
- Do not enable bypass permissions, push, deploy or create paid resources.
- Example: `/devpipeline:build-project Build a full-stack AI-powered document assistant using Next.js, FastAPI, PostgreSQL, pgvector and LangGraph. Users upload PDFs and ask questions with source citations. Use a balanced profile.`
