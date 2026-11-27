---
name: project-doctor
description: "Run DevPipeline environment diagnostics: Claude Code version, dynamic workflow availability, plugin/agent/skill/workflow discovery, UI UX Pro Max and MCP availability, required executables, configuration and schema validity, hook validity, project permissions and known limitations (statuses OK/WARNING/MISSING/UNSUPPORTED/ERROR)."
argument-hint: "[--dir PATH] [--mcp]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/validate_config.py *)
---

# /devpipeline:project-doctor

User input: $ARGUMENTS

## Steps
1. Run the diagnostics (add `--mcp` only if the user asked to health-check MCP servers — it starts them):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py" --project "<dir or cwd>" [--mcp]
   ```
2. Present the table grouped by status. For every ERROR / MISSING / UNSUPPORTED line, give the concrete fix:
   - `plugin:devpipeline MISSING` → run `<devpipeline repo>/scripts/install.sh` (or start Claude with `--plugin-dir <repo>`), then restart Claude Code or run `/reload-plugins`.
   - `skill:ui-ux-pro-max MISSING` → `<repo>/scripts/install.sh --with-ui-ux-pro-max` (official marketplace `nextlevelbuilder/ui-ux-pro-max-skill`).
   - `dynamic-workflows UNSUPPORTED` → enable "Dynamic workflows" in `/config` (Pro plan) or remove `disableWorkflows`; otherwise DevPipeline runs in fallback mode (see docs/WORKFLOWS.md).
   - `claude UNSUPPORTED` → update Claude Code (`claude update`) — ask the user; never update automatically.
   - `exe:node MISSING/WARNING` → Playwright MCP needs Node.js ≥ 20.
   - `config ERROR` → show the validator errors (`validate_config.py --project <dir>`).
3. Optional tools reported as WARNING (ruff, pytest, semgrep, gitleaks, pip-audit…) are not required; checks depending on them will report NOT_RUN instead of PASS. Recommend installing the ones relevant to the user's stack, but do not install anything without approval.
4. Mention the known limitations the doctor lists (budget limits are not hard monetary limits; plugin agents ignore `permissionMode`/`mcpServers`/`hooks` frontmatter, so enforcement uses plugin hooks keyed on `agent_type`).

## Notes
- Read-only diagnostics; never prints secrets (settings files are inspected only for `disableWorkflows`).
- If `claude plugin details devpipeline` does not list the workflows, open `/workflows` in a session to confirm discovery; restart Claude Code after installing or updating the plugin.
- After `install.sh` or `update.sh`, restart Claude Code (or `/reload-plugins` and `/reload-skills`) so agents, skills, hooks, MCP servers and workflows are reloaded.
- To validate a single project's DevPipeline configuration only: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validate_config.py" --project "<dir>"`.
- Doctor exit code 0 means no blocking problem; WARNING lines never fail it.
- Typical healthy output ends with `DevPipeline doctor: healthy`.
- MCP health checks (`--mcp`) launch `npx @playwright/mcp` and contact `mcp.context7.com`; skip them on offline machines.
- The doctor also confirms the hook guard's fast path, i.e. that DevPipeline hooks are inert for non-DevPipeline sessions.
- Use the JSON form (`--json`) when you need to parse results.
