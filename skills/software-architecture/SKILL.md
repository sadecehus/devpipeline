---
name: software-architecture
description: "Architecture method for DevPipeline planning: choosing the simplest viable architecture, contract-first boundaries, ownership maps and acyclic task DAGs that allow safe parallel work in git worktrees, ADRs and test strategy."
user-invocable: false
---

# Software architecture (DevPipeline)

## Activation criteria
Load when producing or revising `architecture.json`, `tasks.json`, contracts or ADRs (Software Architect), or when a contract-change repair task is assigned.

## Required inputs
`requirements.json` (ids, ACs, features, stack_preferences), PRD.md, existing repository structure and conventions (existing-project mode), `config/stack-registry.json` (what can be verified automatically).

## Decision tree
1. **Is there an existing codebase?** Yes → keep stack, structure, naming and test framework; plan the smallest change set. Replacing anything needs `stack_change_justification` + an ADR.
2. **Did the user name a stack?** Use it. Otherwise defaults: web app → Next.js + TypeScript + Tailwind + shadcn/ui; API → FastAPI (Python) or Node/Express/Nest when the frontend is TS-only; DB → PostgreSQL (SQLite for single-user/prototype).
3. **Deployment shape:** one deployable (modular monolith) unless independent scaling, separate teams or different runtimes are *required*. Microservices, Kubernetes, message queues, Redis, vector DBs, multi-agent AI → only with a requirement id that needs them, recorded in an ADR.
4. **AI features?** Single model call with structured output → plain service. Multi-step with branching/human approval/persistence → LangGraph. Retrieval over user documents → RAG (pgvector if PostgreSQL is already there).
5. **Mobile?** Only if a requirement says so; share the API contract.

## Contract-first rules
- Every cross-agent boundary gets a contract file in `.devpipeline/contracts/`: OpenAPI 3.1 for HTTP (`openapi.yaml`), JSON Schema or TS/Python type files for shared types, an error contract (`{"error": {"code", "message", "details"}}` + status code table).
- Contracts are finished in the first tasks (kind `contract`) before dependent implementation starts. Frontend may mock from the contract (MSW) while the backend is built; integration tests always hit the real backend.
- Contract changes after approval: architect repair task + entry in `contract-changes.json` (what, why, affected tasks).

## Ownership and task DAG
- `owned_paths` are repository-relative (`frontend/src/features/auth/`, `backend/app/api/`); directories end with `/`.
- Two tasks that can run concurrently (no dependency path) must have **disjoint** owned paths; otherwise the runtime serialises them (reported as a warning).
- Root/shared files (package.json, lockfiles, pyproject.toml, docker-compose.yml, tsconfig) belong to exactly one task, or appear in `shared_paths` of tasks ordered by dependencies.
- Typical DAG: `contract` → (`design`, `database`) → (`backend`, `frontend`, `ai`) in parallel → integration is automatic → QA is automatic. Do not create QA/review/integration tasks — the workflow runs them.
- Size: one task ≈ one feature slice or component an agent can finish and test in one session. Each task lists requirement and AC ids it satisfies; every must/should requirement is covered.
- `validation_commands` reference scripts that the task itself creates (e.g. `npm run test -- auth`).

## architecture.json essentials
`project_type`, `style`, `stack`, `features` (truthful — drives mandatory gates), `components[]` (name, responsibility, paths, owner_agent), `api_contracts[]`, `data_models[]`, `env_vars[]` (names, `secret` flag, never real values), `ownership[]`, `required_agents[]` + `agent_rationale`, `test_strategy`, `checks[]` (only real project scripts), `app_start` (`command`, `cwd`, `url`, `healthcheck`) so QA can boot the app, `adrs[]`, `risks[]`, `security_notes[]`, `ai_architecture` (models, prompts, tools, state, retrieval) when AI is present.

## ADRs
Use `templates/ADR.md`: context, decision, alternatives considered, consequences. Write one for every non-default or expensive choice and for every rejected user preference.

## Anti-patterns
- Layer cakes (controller→service→manager→repository→dao) without need; generic "core/utils" dumping grounds.
- Shared mutable files edited by many parallel tasks.
- Tasks named "implement backend" covering everything; tasks without ACs.
- Omitting tests, auth or error handling from the plan to "keep it simple".
- Picking technology for novelty; adding infrastructure that no requirement needs.

## Validation
Before finishing: no dependency cycles, all dependencies exist, every task agent appears in `required_agents`, ownership disjoint for independent tasks, every must requirement covered, every env var documented, contracts exist for every boundary. The coordinator's `validate-plan` must pass.

## Completion criteria
architecture.json + tasks.json validate; ARCHITECTURE.md explains structure, data flow and decisions; ADRs exist; contracts are complete enough that frontend/backend/AI engineers never need to guess.

## References
- C4 model: https://c4model.com/
- OpenAPI 3.1: https://spec.openapis.org/oas/v3.1.0
- ADRs: https://adr.github.io/
- Modular monolith: https://www.kamilgrzybek.com/blog/posts/modular-monolith-primer
- Twelve-Factor App (config, backing services): https://12factor.net/

## Example (fragment of tasks.json)
```json
{"tasks": [
 {"id": "TASK-001", "agent": "software-architect", "title": "API contract", "kind": "contract", "requirements": ["REQ-001"], "dependencies": [], "owned_paths": [], "acceptance_criteria": [], "status": "PENDING", "attempt": 0},
 {"id": "TASK-002", "agent": "backend-engineer", "title": "Auth endpoints", "requirements": ["REQ-001"], "dependencies": ["TASK-001"], "owned_paths": ["backend/app/auth/", "backend/tests/auth/"], "shared_paths": ["backend/pyproject.toml", "backend/uv.lock"], "acceptance_criteria": ["AC-001"], "status": "PENDING", "attempt": 0},
 {"id": "TASK-003", "agent": "frontend-engineer", "title": "Login page", "requirements": ["REQ-001"], "dependencies": ["TASK-001"], "owned_paths": ["frontend/src/features/auth/"], "acceptance_criteria": ["AC-001"], "status": "PENDING", "attempt": 0}
]}
```
