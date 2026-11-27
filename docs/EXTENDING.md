# Extending DevPipeline

All extension points are data-driven; the orchestration engine does not hardcode agent lists or gates.

## Add a new agent
1. Create `agents/<name>.md` with frontmatter (`name`, `description`, `tools`/`disallowedTools` — always disallow `Agent` — `model`, `maxTurns`, `skills`) and the 12 sections (`## Role and expertise`, `## Responsibilities`, `## Inputs`, `## Outputs`, `## File ownership`, `## Prohibited operations`, `## Standards`, `## Validation steps`, `## Communication contract`, `## Definition of done`, `## Failure and escalation`, `## Security requirements`).
2. Run `python3 scripts/sync_agent_contract.py` to append the common contract.
3. Register it in `config/agent-registry.json → agents` with `category` (model routing), `role` (`implementation` | `qa` | `review` | `planning` | `integration` | `documentation`), `activation` (`always`, `any_feature`, `profiles`, `min_implementation_agents`) and optionally `lock`.
4. Implementation agents: add to `repairable_agents` and, if relevant, to `routing`/`routing_fallbacks`.
5. QA agents: add the report kind to `QA_KIND` in `scripts/workflow_src/engine.js` and `QA_KINDS` in `scripts/devpipeline.py`, extend the `kind` enum in `schemas/test-report.schema.json`, add a `report` gate in `config/quality-gates.json`, then `python3 scripts/build_workflows.py`.
6. `python3 scripts/validate_config.py && python3 -m unittest discover -s tests/unit`.

## Add a new skill
`skills/<name>/SKILL.md` with frontmatter (`name`, `description`, `user-invocable: false` for knowledge skills) and sections Activation criteria, Required inputs, guidance/decision trees, Anti-patterns, Validation, Completion criteria, References (≥ 450 words; large material in sibling reference files). Preload it by adding `devpipeline:<name>` to the relevant agents' `skills:`.

## Add an MCP server
Shipped (all sessions with the plugin enabled): add it to `.mcp.json` with a pinned version and no credentials, declare hosts in `plugin.json → network.hosts`, add an entry to `config/integrations.json` (capabilities, required_for, fallback), document it in docs/MCP.md and reference its tool prefix (`mcp__plugin_devpipeline_<server>__`) in the consuming agent prompt. Opt-in: document the `claude mcp add …` command instead of shipping it.

## Register a technology stack
Add a stack to `config/stack-registry.json → stacks` (detect files, install command, script names or tool commands) and its executables to `allowed_executables`. If the stack needs custom discovery, extend `discover()` in `scripts/run_checks.py`; if its test output format differs, extend `parse_counts()` (add a unit test).

## Create a test adapter
A test adapter is (a) discovery in `run_checks.discover()` producing `{name, kind, cwd, command}` and (b) a count parser in `parse_counts()`. Kinds map to gates through `config/quality-gates.json → kinds`.

## Add a quality gate
Append to `config/quality-gates.json → gates`: `id`, `description`, `source` (`checks` + `kinds`/`names`/`names_prefix`, `report` + `report_kind`, `review` + `review_kind`, or one of `tasks`, `coverage`, `documentation`, `issues`, `plan_validation`), `applies` rules, `default` level. Profiles override levels via `gates`. Map an AC verification method to it in `VERIFICATION_TO_GATE` (`scripts/quality_gate.py`) if requirements should be able to force it.

## Modify agent selection rules
Edit `activation` in `config/agent-registry.json`. Rules are OR-combined; `lock: true` prevents removal by architect/config once a rule fires. Selection is computed in `scripts/dpcore/selection.py` and recorded per run (`agent_decisions`).

## Modify model routing
`config/model-routing.json`: `default` (category → alias), `profiles.<p>` overrides, `effort`. Per project: `.devpipeline/config.json → overrides.model_routing.<agent>`. Only documented aliases (`opus`, `sonnet`, `haiku`, `fable`, `inherit`).

## Customise execution profiles
Edit/add `config/profiles/<name>.json` (any key of `config.schema.json`); add the name to `PROFILES` in `scripts/dpcore/config.py` and to the `profile` enums in the schemas.

## Extend workflows
Edit `scripts/workflow_src/engine.js` (plain JavaScript: no imports, no `Date.now()`/`Math.random()`/`new Date()`, no Node APIs; every file/command operation goes through `op('<command>')` → `scripts/devpipeline.py`). New deterministic operations are added to `devpipeline.py`'s `ops` table and must print one JSON object via `emit()` (checksum). Add a new entry workflow in `scripts/build_workflows.py → ENTRIES`, regenerate, and cover the behaviour with a scenario in `tests/integration/workflow.test.mjs` (fake agents + real helpers).
