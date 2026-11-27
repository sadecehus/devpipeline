# Troubleshooting

Start with `/devpipeline:project-doctor` (or `python3 scripts/doctor.py --project <dir>`), then `/devpipeline:project-status`.

| Symptom | Cause | Fix |
|---|---|---|
| `/devpipeline:*` commands missing | plugin not installed/enabled or session not reloaded | `scripts/install.sh`, then restart Claude Code or `/reload-plugins` |
| Workflow tool not offered / "Dynamic workflows are not enabled for this session" | `enableWorkflows` unset and off by default for the plan, `disableWorkflows`, `CLAUDE_CODE_DISABLE_WORKFLOWS=1` | enable "Dynamic workflows" in `/config` or `scripts/install.sh --enable-workflows`; check with `tests/smoke/run_runtime_smoke.sh`; otherwise fallback mode (docs/WORKFLOWS.md) |
| Workflow stops at once: "need args {plugin_root, project_dir, run_id}" | workflow launched directly (`/devpipeline:pipeline-build`) | start through `/devpipeline:build-project` (it prepares the run) |
| Run ends `BLOCKED: operator relay for '…' failed the integrity check 3 times` | the relay model altered the helper's JSON while copying it (the operation itself ran once and is recorded) | open `.devpipeline/runs/<run>/ops/relay-failure-*.json` to see the altered fields, then `/devpipeline:resume-project` |
| Run ends `FAILED: deterministic helper '…' could not be executed/relayed` | operator relay failed 3× (permission denied for the Bash call, python missing, relay checksum mismatch) | approve the `python3 …/scripts/devpipeline.py` Bash calls (or allow them), check `python3 --version`, see the run's `events.jsonl`; then `/devpipeline:resume-project` |
| `BLOCKED: DevPipeline was updated while this run was in progress` | the plugin is read from its source folder; helpers changed under a running workflow (protocol handshake stopped it before anything was changed) | `/devpipeline:resume-project` (relaunches with the updated workflow). Avoid editing the DevPipeline folder while runs are active |
| Run FAILED after planning (framework error) and you want to keep the plan | — | `/devpipeline:build-project --dir <dir> --reuse-plan` skips analyst/architect when the existing plan validates |
| `illegal transition X -> Y` | state machine guard | indicates an engine/state mismatch; run `resume` (moves interrupted runs to BLOCKED first) |
| `COMPLETED refused: code changed after the last passing gate` | files changed after the final gate | resume → verification re-runs on the current revision |
| Gate `unit_tests` = SKIPPED | skipped tests in a mandatory suite | implement or remove the skip with an approved requirement change; skips never count as pass |
| Gate = NOT_RUN "no … command discovered" | project lacks the script (e.g. no `test` script) | the owning engineer adds it; DevPipeline never invents script names |
| `dependency_audit` NOT_RUN | scanner missing / offline | install `pip-audit` etc. if you want it; mandatory only in strict |
| `ai_evaluation` BLOCKED | strict profile requires live evaluation but no credentials/approval | provide `ANTHROPIC_API_KEY` (or the provider in use) and `RUN_LIVE_AI_TESTS=1`, or use balanced (`live_ai_evaluation: optional`) |
| `NEEDS_HUMAN_REVIEW: no progress` | same issue fingerprints survived repeated repairs | read the issues in `.devpipeline/runs/<run>/issues/`, decide (requirement change, manual fix), resume |
| `BUDGET_EXHAUSTED` | agent/operator call limit | raise `overrides.max_agent_calls` in `.devpipeline/config.json`, resume |
| Hook denial "DevPipeline guard: …" visible in an agent transcript | agent tried to write outside its owned paths / run an unsafe command | expected protection; if the ownership plan is wrong, fix `tasks.json` via an architect repair |
| Browser QA reports `playwright-mcp available: false` | MCP server not connected for subagents (node missing, first `npx` download blocked) | `node --version` ≥ 20, run `npx -y @playwright/mcp@0.0.86 --help` once, `doctor.py --mcp`; the repeatable Playwright suite still provides e2e evidence |
| `npx playwright install` prompts / fails | browser binaries missing | allow the download (user cache only) or install manually |
| Merge conflicts repeat | overlapping ownership in the plan | architect should make owned paths disjoint or add dependencies |
| Existing project not a git repo | worktree isolation unavailable | `git init && git add -A && git commit -m baseline` before improve, or accept serial mode |
| Stale `.devpipeline/worktrees/*` after a crash | interrupted run | `git worktree list`; resume the run, or `git worktree remove <path>` for DevPipeline paths only |

Logs: `.devpipeline/runs/<run>/events.jsonl` (structured, redacted), `logs/` (check outputs, redacted), `gates/gate-NNN.json`, `agent-results/`, `test-reports/`, `reviews/`, `issues/`, `final-report.md`. Nothing is uploaded anywhere.
