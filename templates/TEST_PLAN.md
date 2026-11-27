# Test plan — {{project_title}}

| AC | Requirement | Level | Test location | Owner | Mandatory |
|---|---|---|---|---|---|
| AC-001 | REQ-001 | unit | `tests/test_x.py::test_y` | backend-engineer | yes |

## Environments
- Local only (`localhost`), isolated test database: {{how created/destroyed}}
- Test accounts/fixtures: {{deterministic, no real data}}

## Quality gates in scope
{{list from config/quality-gates.json after profile resolution}}

## Out of scope / not automatable
{{item}} — reason, how it is covered instead.

## AI evaluation (if applicable)
Thresholds, datasets (positive/negative/edge/adversarial), live-evaluation policy.
