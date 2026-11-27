---
name: testing-standards
description: "Testing and evidence standards for every DevPipeline agent: test pyramid per stack (Vitest/Jest/Testing Library, Pytest, JUnit, xUnit, Go), what counts as evidence (command, cwd, exit code, counts, log), skipped/flaky/mocked test rules, integration tests against real dependencies, and the forbidden ways to make tests pass."
user-invocable: false
---

# Testing standards

## Activation criteria
Any agent that writes or runs tests (engineers, Code QA, Browser QA, AI evaluation, integration).

## Required inputs
Task entry or QA brief (ACs in scope, owned/QA paths), the project's manifests (to discover real test commands), contracts, and the run directory for logs.

## Evidence rules (non-negotiable)
- A check is evidence only with: command, working directory, exit code, start/end time, the content fingerprint it ran against, test counts when applicable, and a log file under `<run_dir>/logs/`.
- Exit code 0 with **0 tests executed** is not a pass. Skipped tests in mandatory suites block completion (`SKIPPED`), they are never PASS.
- An unavailable tool/runner → `NOT_RUN` with the reason. Never write "tests pass" without having run them in this attempt.
- The deterministic runner (`run_checks.py`) re-executes discovered scripts after you; your claims are compared with its results.

## What to test
| Level | Purpose | Typical tools |
|---|---|---|
| Unit | business rules, pure functions, components | Vitest/Jest + Testing Library, Pytest, JUnit 5 + Mockito, xUnit/NUnit, Go `testing` |
| Integration | DB, HTTP layer, queues with real dependencies | Pytest + TestClient/httpx, supertest, Spring MockMvc + Testcontainers, WebApplicationFactory |
| Contract/API | responses match the agreed contract | schemathesis, openapi validators, Pact (only if adopted) |
| E2E | critical user journeys in a real browser | Playwright |
| AI eval | model behaviour | see ai-evaluation |

Cover: acceptance criteria (one or more tests per AC, named after it), edge cases (empty, max length, unicode, boundary numbers), failure handling (dependency down, timeout, invalid input), authorization negatives, data integrity (constraints, transactions), regressions for every fixed bug.

## Writing good tests
Arrange-Act-Assert; one behaviour per test; descriptive names (`test_rejects_expired_token`); deterministic (fixed clock/seed injected, no real network, no sleeps — use polling/web-first assertions); isolated data per test (transactions rolled back, unique ids); fast unit tests; fixtures/factories instead of shared mutable globals.

## Mocks
Mock at system boundaries (HTTP to third parties, model providers, clock). Mocks are never the only evidence that an integration works — at least one test exercises the real integration (real DB, real backend for the frontend in e2e).

## Forbidden
Deleting/disabling failing tests, `skip`/`xfail`/`.only` added to get green, weakening assertions (`toBeTruthy` instead of exact values, catching assertion errors), editing production code from QA roles, lowering coverage thresholds, converting mandatory checks to optional, fabricating output, retrying flaky tests until green without fixing the cause (record flakiness as an issue).

## Stack commands (use only if defined in the project)
- Node: `npm ci` then `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`, `npm run test:e2e` (pnpm/yarn equivalents by lockfile).
- Python: `uv sync --frozen`, `uv run ruff check .`, `uv run mypy .`, `uv run pytest -q -rs`.
- Java: `mvn -B verify` / `./gradlew build`. .NET: `dotnet restore`, `dotnet build --no-restore`, `dotnet test --no-build`. Go: `go vet ./...`, `go test ./...`.
Check `package.json` scripts / `pyproject.toml` before running; never invent script names.

## Completion criteria
Every AC in scope has an executed test with evidence; suites pass with no skipped mandatory tests; failures are reported as issues with reproduction steps and assigned owners.

## References
- Testing Library guiding principles: https://testing-library.com/docs/guiding-principles
- Pytest: https://docs.pytest.org/ · Vitest: https://vitest.dev/ · Jest: https://jestjs.io/docs/getting-started
- JUnit 5: https://junit.org/junit5/docs/current/user-guide/ · Testcontainers: https://testcontainers.com/
- xUnit: https://xunit.net/ · Go testing: https://pkg.go.dev/testing
- Martin Fowler, Test Pyramid: https://martinfowler.com/articles/practical-test-pyramid.html
