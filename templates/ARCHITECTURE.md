# Architecture — {{project_title}}

> Machine-readable source: `.devpipeline/architecture.json`, task graph `.devpipeline/tasks.json`.

## 1. Context
{{system purpose, users, external systems (C4 level 1)}}

## 2. Style and rationale
{{modular monolith / other}} — chosen because {{requirement ids}}. Rejected alternatives: see ADRs.

## 3. Technology stack
| Layer | Choice | Version policy | Reason |
|---|---|---|---|

## 4. Components and ownership
| Component | Responsibility | Paths | Owner agent |
|---|---|---|---|

## 5. Contracts
- HTTP API: `.devpipeline/contracts/openapi.yaml`
- Shared types: `.devpipeline/contracts/{{file}}`
- Error contract: `{"error": {"code": "...", "message": "...", "details": {}}}` with status code table.

## 6. Data model
{{entities, relations, constraints, indexes}}

## 7. Authentication & authorization
{{mechanism, session/token handling, object-level checks}}

## 8. AI architecture (if applicable)
{{models via env, prompts, tools + authorization, state/graph, retrieval, evaluation, limits}}

## 9. Environment variables
| Name | Secret | Required | Description |
|---|---|---|---|

## 10. Test strategy
| Level | Scope | Tooling | Command |
|---|---|---|---|

## 11. Task DAG
{{mermaid graph of TASK ids and dependencies}}

## 12. Risks
- {{risk}} → {{mitigation}}

## 13. ADRs
- ADR-001 {{title}}
