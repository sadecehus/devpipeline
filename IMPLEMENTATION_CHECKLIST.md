# DevPipeline — Implementation Checklist (roadmap)

Source of requirements: `../master_prompt.md` (sections 1–50).
Status legend: `[ ]` todo · `[x]` done and verified · `[~]` done with documented limitation · `[!]` blocked (reason recorded)

## Phase 1 — Environment inspection (§3)
- [x] OS/arch: macOS 26.6.2 (Darwin 25.6.0) arm64
- [x] Claude Code 2.1.332 (≥ 2.1.248 needed for bundled workflow-authoring; ≥ 2.1.269 for workflow concurrency env)
- [x] Dynamic Workflow API verified from bundled `workflow-authoring` reference inside the installed binary:
      `agent(prompt,{label,phase,schema,model,effort,isolation:'worktree',agentType})`, `parallel(thunks)`, `pipeline(items,...stages)`,
      `phase()`, `log()`, `args`, `budget`, `workflow()`; no fs/shell/import; `Date.now()/Math.random()/new Date()` throw.
- [x] Workflow tool inputs: `script | name | scriptPath`, `args`, `resumeFromRunId`; Workflow tool unavailable inside subagents.
- [x] Plugin spec (plugin.json, marketplace.json, namespacing `plugin:agent`, `${CLAUDE_PLUGIN_ROOT}`), agent frontmatter, skill frontmatter, hooks events/JSON
- [x] Plugin agents ignore `hooks`, `mcpServers`, `permissionMode` → hooks/MCP shipped at plugin level
- [x] Tools: node 26.11.1, npm 11.20, python 3.13.0, git 2.50.1, docker 29.1.3, uv 0.11.30, pnpm 12.11.2, java 26, dotnet 9.0.306; missing: ruff, pytest, semgrep, gitleaks, yarn, bun, go
- [x] Existing config: user settings with 2 plugins (vercel, frontend-design), user hooks (Notification, PermissionRequest, SessionEnd, SessionStart, Stop), no user agents/commands/workflows
- [x] UI UX Pro Max: not installed; repo reviewed (v2.15.0, commit 11ca2b3, MIT, skills only, no hooks/MCP at plugin root)
- [x] Playwright MCP @playwright/mcp 0.0.86, chrome-devtools-mcp 1.14.0, context7 (remote HTTP, key optional)

## Phase 2 — Architecture & installation plan (§4, §5)
- [x] Location: `~/Desktop/development/devpipeline` (git repo, single canonical source) — documented in docs/ARCHITECTURE.md
- [x] Packaging: plugin `devpipeline` + local marketplace `devpipeline-local` (same repo root), user scope install via official CLI
- [x] Optional user-level mode: `CLAUDE_CODE_PLUGIN_DIRS` / `--plugin-dir` (no file copies) — documented
- [x] Runtime design: workflow JS owns control flow; deterministic Python helpers own file I/O, validation, gates; invoked via narrow `pipeline-operator` agent

## Phase 3 — Configuration & JSON schemas (§31, §37, §38)
- [x] schemas/*.schema.json (10) + stdlib JSON-schema validator (`scripts/dpcore/jsonschema_lite.py`, keyword-coverage test)
- [x] config/defaults.json, model-routing.json, quality-gates.json, stack-registry.json, agent-registry.json, state-machine.json, integrations.json, profiles/{fast,balanced,strict}.json
- [x] validate_config.py (framework + project)

## Phase 4 — Agents (§6–§25)
- [x] 19 specialist agents + pipeline-operator; 12 sections each (validated); common contract synced from docs/AGENT_CONTRACT.md

## Phase 5 — Skills (§26)
- [x] 7 command skills that run `devpipeline.py` and launch the real workflow (verified: project-status in real runtime)
- [x] 17 knowledge skills (≥450 words, activation/inputs/decision trees/anti-patterns/validation/completion/references)
- [x] UI UX Pro Max 2.15.0 reviewed and installed via its official marketplace

## Phase 6 — MCP (§27)
- [x] .mcp.json: Playwright MCP 0.0.86 (headless, isolated) + Context7 (remote, no key) — both "✔ Connected" in `claude mcp list`
- [x] config/integrations.json; Chrome DevTools/GitHub/Figma/DB documented as opt-in

## Phase 7 — State & helper scripts (§32)
- [x] state_manager.py (atomic+locked, legal transitions, COMPLETED guard, checkpoints, events, resume)
- [x] validate_contracts.py, selection, worktree_manager.py, run_checks.py, redact_secrets.py, report_generator.py, devpipeline.py, doctor.py

## Phase 8 — Workflows (§35)
- [x] build/improve/audit/repair workflows generated from one engine (scripts/workflow_src/engine.js)
- [x] fallback mode documented with reduced guarantees (docs/WORKFLOWS.md)
- [x] real-runtime smoke: Workflow runtime + operator relay/checksum + agentType + hooks (tests/smoke/run_runtime_smoke.sh — 4/4 PASS)

## Phase 9 — Quality gate & repair engine (§33, §34)
- [x] quality_gate.py (7 statuses, evidence validation, code/prod fingerprints, rule-based N/A, requirement-forced gates, baseline/pre-existing)
- [x] issue normalisation/dedup/fingerprints, routing, repair planning, no-progress + budget stops

## Phase 10 — Hooks & permissions (§36, §43)
- [x] hooks/hooks.json + hook_guard.py (only devpipeline:* agents with an active run; registry for cwd-independent lookup)

## Phase 11 — User commands (§39)
- [x] /devpipeline:build-project, improve-project, audit-project, repair-project, project-status, resume-project, project-doctor

## Phase 12 — Framework tests (§44)
- [x] 80 Python unit tests
- [x] 20 mocked workflow scenarios (all 11 required + integrity, approval, full-stack, audit, repair, plan-approval bypass, repair-resume, superseded repair, user-checkout change)
- [x] installer idempotency test (stub CLI)

## Phase 13 — Smoke test (§45)
- [x] examples/fullstack-todo, backend-api, ai-rag-assistant + tests/smoke/test_examples.py
- [x] small real Claude runtime smoke passed (no full example pipeline run, to avoid large usage — command documented)

## Phase 14 — Documentation (§47)
- [x] README + docs/{ARCHITECTURE,AGENTS,WORKFLOWS,SKILLS,MCP,CONFIGURATION,TROUBLESHOOTING,EXTENDING,AGENT_CONTRACT}.md + CHANGELOG + VERSION

## Phase 15 — Installation & final verification (§46, §49)
- [x] settings.json backed up (~/.claude/backups/devpipeline-*), installed via scripts/install.sh (idempotent), doctor healthy
- [x] claude plugin validate passes; 20/20 agents, 24/24 skills, 4/4 workflows discovered
- [x] Dynamic workflows enabled by the user via /config (`enableWorkflows=true`); doctor: OK
- [x] independent verification subagent review: MOSTLY COMPLETE → 9 important + minor gaps fixed, each with a regression test (unit 80, integration 20); re-verification: all 9 fixed, 1 new + 5 minor found → fixed except the documented Bash-heuristic limitation; real-runtime smoke re-run incl. large relays (5/5 PASS)
