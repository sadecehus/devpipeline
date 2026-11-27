# Skills

## Command skills (user-invocable, `disable-model-invocation: true` where they launch runs)
| Command | What it executes |
|---|---|
| `/devpipeline:build-project <idea> [options]` | `devpipeline.py prepare --mode build --new` → Workflow `workflows/build-project.js` |
| `/devpipeline:improve-project <change> [--dir]` | git/manifests inspection → `prepare --mode improve` → `workflows/improve-project.js` |
| `/devpipeline:audit-project [--dir]` | `prepare --mode audit` → `workflows/audit-project.js` |
| `/devpipeline:repair-project [--from RUN] [ISSUE…]` | shows findings, asks for approval → `prepare --mode repair --source-run --issues` → `workflows/repair-project.js` |
| `/devpipeline:project-status [--dir]` | `devpipeline.py status` (read-only) |
| `/devpipeline:resume-project [answers]` | `devpipeline.py resume` (identity, external changes, stale evidence, approvals) → relaunch |
| `/devpipeline:project-doctor [--mcp]` | `scripts/doctor.py` |

Options understood by build/improve: `--dir`, `--profile fast|balanced|strict`, `--frontend`, `--backend`, `--database`, `--ai-framework`, `--max-iterations N`, `--concurrency N`. Input may be Turkish or English ("hızlı prototip" → fast, "production / güvenlik kritik" → strict).

The skills never implement code themselves; they only prepare, launch and report. Their `allowed-tools` pre-approve only the DevPipeline helper scripts; the Workflow launch itself still asks the user for approval (cost control).

## Knowledge skills (`user-invocable: false`, preloaded via agent `skills:`)
| Skill | Loaded by | Content highlights |
|---|---|---|
| software-architecture | architect | stack decision tree, contract-first, ownership/DAG rules, ADRs |
| frontend-engineering | frontend, designer | Next.js/TS/Tailwind/shadcn defaults, states, forms, WCAG 2.2 AA, anti-generic design rules |
| backend-engineering | backend | per-framework notes, validation, authz per object, error contract, AI boundary |
| database-engineering | database | schema/constraints/indexes, migrations per tool, safe seeds, isolated test DBs, pgvector |
| ai-engineering | AI engineer | when (not) to use agents, providers, structured outputs, tools/MCP, reliability, security, credential-free tests |
| langchain-engineering | AI engineer | v1 APIs (`init_chat_model`, `create_agent`, `with_structured_output`, middleware), deprecated APIs to avoid, fake models |
| langgraph-engineering | AI engineer | typed state + reducers, conditional edges, `Send`, `Command`, checkpointers/thread ids, Store, `interrupt`, recursion limits, graph tests |
| rag-engineering | AI engineer | ingestion, structure-aware chunking, embedding versioning/re-index, ACL-filtered retrieval, hybrid search, citations, insufficient evidence |
| ai-evaluation | AI evaluation | thresholds first, datasets, deterministic vs judge, live policy, honest reporting |
| mobile-engineering | mobile | framework choice, permissions, secure storage, emulator approval rules |
| testing-standards | engineers, QA, integration | evidence rules, skipped/zero-test rules, mocks, forbidden practices, stack commands |
| browser-testing | browser QA | MCP availability check, journeys × viewports, console/network/a11y, Playwright suite |
| security-engineering | security, backend | threat model, OWASP checklists, scanners (NOT_RUN ≠ PASS), finding format |
| performance-engineering | performance | budgets, Lighthouse/k6/EXPLAIN, repeat & median, lab vs production |
| devops-engineering | devops | minimal non-root images, compose, env templates, CI without deploy |
| code-review | code reviewer, final reviewer | review procedure, blocking criteria, acceptance matrix, freshness |
| technical-documentation | writer | README structure, verified commands, env vars without values, documentation gate |

## Third-party skill: UI UX Pro Max
Installed from its official marketplace (`claude plugin marketplace add nextlevelbuilder/ui-ux-pro-max-skill` + `claude plugin install ui-ux-pro-max@ui-ux-pro-max-skill`) by `scripts/install.sh`. Reviewed before installation: version 2.15.0, commit `11ca2b3bdd555649f278ee348c32a9302b24dcf1`, MIT license, ships only skills (no hooks, no MCP servers at the plugin root); core scripts (`scripts/search.py`, `core.py`, `design_system.py`) use the Python standard library and no network. It is preloaded into the UI/UX Designer and Frontend Engineer as `ui-ux-pro-max:ui-ux-pro-max`; the designer must record the queries it ran in DESIGN_SYSTEM.md. DevPipeline does not ship a replacement with the same name.
