# Agents

20 agent definitions in `agents/*.md` (namespaced `devpipeline:<name>` by the plugin). Activation rules live in one place, `config/agent-registry.json`; the common contract (read-before-acting, ownership, evidence, structured result, no delegation, safety, untrusted input) is copied from `docs/AGENT_CONTRACT.md` into every agent by `scripts/sync_agent_contract.py`.

Every specialist prompt contains: role and expertise, responsibilities, inputs, outputs, file ownership, prohibited operations, standards, validation steps, communication contract, definition of done, failure and escalation, security requirements (enforced by `validate_config.py`).

| Agent | Role | Activation | Default model | Tools | Preloaded skills |
|---|---|---|---|---|---|
| `devpipeline:orchestrator` | advisor | on demand | sonnet | Read, Grep, Glob | — |
| `devpipeline:product-analyst` | planning | always (locked) | sonnet | Read, Grep, Glob, Write, Edit, Skill | — |
| `devpipeline:software-architect` | planning | always (locked) | opus | inherits (minus Agent) | devpipeline:software-architecture |
| `devpipeline:ui-ux-designer` | implementation | features: frontend, mobile, browser_ui | sonnet | inherits (minus Agent) | ui-ux-pro-max:ui-ux-pro-max, devpipeline:frontend-engineering |
| `devpipeline:frontend-engineer` | implementation | features: frontend | sonnet | inherits (minus Agent) | devpipeline:frontend-engineering, devpipeline:testing-standards, ui-ux-pro-max:ui-ux-pro-max |
| `devpipeline:backend-engineer` | implementation | features: backend | sonnet | inherits (minus Agent) | devpipeline:backend-engineering, devpipeline:testing-standards, devpipeline:security-engineering |
| `devpipeline:database-engineer` | implementation | features: database | sonnet | inherits (minus Agent) | devpipeline:database-engineering, devpipeline:testing-standards |
| `devpipeline:ai-engineer` | implementation | features: ai, rag (locked) | sonnet | inherits (minus Agent) | devpipeline:ai-engineering, devpipeline:rag-engineering, devpipeline:langgraph-engineering, devpipeline:langchain-engineering |
| `devpipeline:mobile-engineer` | implementation | features: mobile | sonnet | inherits (minus Agent) | devpipeline:mobile-engineering, devpipeline:testing-standards |
| `devpipeline:devops-engineer` | implementation | features: devops | sonnet | inherits (minus Agent) | devpipeline:devops-engineering |
| `devpipeline:integration-engineer` | integration | ≥1 implementation agent (locked) | sonnet | inherits (minus Agent) | devpipeline:testing-standards |
| `devpipeline:browser-qa-engineer` | qa | features: frontend, browser_ui (locked) | sonnet | inherits (minus Agent) | devpipeline:browser-testing, devpipeline:testing-standards |
| `devpipeline:code-qa-engineer` | qa | ≥1 implementation agent (locked) | sonnet | inherits (minus Agent) | devpipeline:testing-standards |
| `devpipeline:security-engineer` | qa | features: authentication, payments, ai; profiles: balanced, strict (locked) | sonnet | inherits (minus Agent) | devpipeline:security-engineering |
| `devpipeline:ai-evaluation-engineer` | qa | features: ai, rag (locked) | sonnet | inherits (minus Agent) | devpipeline:ai-evaluation |
| `devpipeline:performance-engineer` | qa | features: performance_requirements; profiles: strict | sonnet | inherits (minus Agent) | devpipeline:performance-engineering |
| `devpipeline:code-reviewer` | review | profiles: balanced, strict (locked) | opus | Read, Grep, Glob, Bash, Write | devpipeline:code-review |
| `devpipeline:technical-writer` | documentation | always (locked) | haiku | Read, Grep, Glob, Bash, Write, Edit, Skill | devpipeline:technical-documentation |
| `devpipeline:final-reviewer` | review | always (locked) | opus | Read, Grep, Glob, Bash, Write | devpipeline:code-review |
| `devpipeline:pipeline-operator` | internal | internal | haiku | Bash, Read | — |

*locked* = once a rule activates the agent, neither the architect nor config can remove it (e.g. AI evaluation for AI projects, browser QA for web UIs, final review always).

## Model routing
Category defaults (`config/model-routing.json`): planning sonnet · architecture opus · orchestration sonnet · design/implementation/integration/testing sonnet · review opus · final review opus · documentation haiku · operator haiku. `fast` downgrades architecture/review to sonnet; `strict` upgrades planning/orchestration to opus. The workflow passes the resolved alias per call (`agent({model})`); per-agent overrides: `.devpipeline/config.json → overrides.model_routing`.

## Frontmatter fields used and why some are not
Used: `name`, `description`, `tools` / `disallowedTools` (least privilege; reviewers cannot Edit; every agent loses `Agent` to prevent recursive delegation), `model`, `effort`, `maxTurns`, `skills`, `color`.
Not used, with reason:
- `permissionMode`, `mcpServers`, `hooks` — ignored for plugin agents by Claude Code (security rule). Replaced by plugin `hooks/hooks.json` (guard keyed on `agent_type`) and plugin `.mcp.json`.
- `isolation: worktree` — native worktrees branch from the default branch; DevPipeline needs tasks to branch from the current *integration* HEAD and to record base/result revisions, so `scripts/worktree_manager.py` manages worktrees.
- `memory`, `background` — runs are coordinated by the workflow; persistent memory across projects would leak context between unrelated projects.

## Boundaries that are easy to get wrong
- **AI engineer vs backend engineer:** AI engineer owns prompts, model config, chains/graphs, retrieval, tool contracts and AI eval fixtures; backend owns HTTP transport, auth, persistence and wiring to the AI service interface.
- **QA vs engineers:** QA agents may only write tests in QA paths (`tests/`, `e2e/`, `evals/`, `*.test.*`, `*.spec.*`, `playwright.config.*`) in the integration worktree; production defects become issues routed to owners.
- **Reviewers** write only their verdict file; their approval is necessary but never sufficient (the deterministic gate must pass).
- **pipeline-operator** may only run `python3 <plugin>/scripts/<helper>.py …` (hook-enforced) and relays checksum-protected JSON.

## Issue routing (`config/agent-registry.json → routing`)
visual → ui-ux-designer · functional/accessibility → frontend-engineer · api → backend-engineer · database → database-engineer · ai_quality/ai_security → ai-engineer · infrastructure → devops-engineer · integration → integration-engineer · documentation → technical-writer · contract/requirements → software-architect (+ dependent engineer tasks) · security/performance/build/test/code_quality → owner of the affected files (from task ownership). Fallback chain when the target agent is not active: designer→frontend, devops→backend, database→backend, frontend↔backend.
