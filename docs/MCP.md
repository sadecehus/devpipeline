# MCP and external integrations

Registry: `config/integrations.json`. Shipped servers are declared in the plugin's `.mcp.json` and start in sessions where the plugin is enabled; tool names are `mcp__plugin_devpipeline_<server>__<tool>`. Plugin agents cannot declare their own `mcpServers`; workflow subagents reach session MCP tools through ToolSearch, and the Browser QA agent is instructed to **verify** availability in its own context and to report `playwright-mcp` as available/ran in its report.

| Integration | Status | Configuration | Capabilities | Fallback |
|---|---|---|---|---|
| **Playwright MCP** (required for web) | shipped | `npx -y @playwright/mcp@0.0.86 --headless --isolated --output-dir ${CLAUDE_PLUGIN_DATA}/playwright-output` (pinned, in-memory profile, no shared state between sessions) | navigate, click, type, snapshot, screenshot, console, network, resize | repeatable Playwright suite run by `run_checks.py` (`e2e` gate); exploratory checks NOT_RUN |
| **Context7** (recommended) | shipped | remote HTTP `https://mcp.context7.com/mcp`, no API key configured (rate-limited free tier; add a key yourself via `claude mcp add` headers if needed — never hardcode it) | current library docs | WebFetch on official docs URLs listed in the skills |
| **UI UX Pro Max** (required for UI) | installed by `install.sh` (skill, not MCP) | official marketplace | design-system search | designer rules in frontend-engineering; DESIGN_SYSTEM.md must state the gap |
| Chrome DevTools MCP | opt-in | `claude mcp add chrome-devtools --scope user -- npx -y chrome-devtools-mcp@1.14.0 --isolated --headless --no-usage-statistics` | performance traces, deep console/network debugging | Playwright MCP console/network |
| GitHub, Figma, database MCPs | not enabled | only on explicit user request with their own credentials | — | local git; design specs in DESIGN_SYSTEM.md; isolated test databases |

Rules: optional integrations are never enabled automatically; production databases are never exposed; credentials are never written into MCP config (use the server's own auth/OS keychain); GitHub access is not needed for local development.

Verification: `python3 scripts/doctor.py --mcp` (starts the servers via `claude mcp list`).
