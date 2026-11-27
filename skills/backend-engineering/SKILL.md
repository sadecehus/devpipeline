---
name: backend-engineering
description: "Backend implementation standards for DevPipeline across FastAPI, Django, Express, NestJS, Spring Boot, ASP.NET Core and Go: contract-exact endpoints, validation, authentication/authorization, error contracts, transactions, timeouts, rate limiting, structured logging, AI-service integration boundary and testing."
user-invocable: false
---

# Backend engineering

## Activation criteria
Backend Engineer tasks; Code Reviewer/Security Engineer when judging server code.

## Required inputs
Task entry, `.devpipeline/contracts/openapi.yaml` (or other contract), data model spec, auth design from ARCHITECTURE.md, env var list.

## Framework decision notes
- **FastAPI**: Pydantic v2 models for request/response, `Depends` for auth/db sessions, `APIRouter` per feature, `HTTPException` mapped to the error contract via exception handlers, `uv` for dependency management, pytest + httpx `AsyncClient`/`TestClient`.
- **Django/DRF**: serializers for validation, permissions classes for authz, `transaction.atomic`, `select_related/prefetch_related` against N+1.
- **Express**: zod validation middleware, central error handler, `helmet`, async error wrapping; supertest for tests. **NestJS**: DTOs + `ValidationPipe({whitelist: true, forbidNonWhitelisted: true})`, guards for authz.
- **Spring Boot**: Bean Validation (`@Valid`), `@ControllerAdvice` error mapping, `@Transactional` on service methods, Spring Security method security; JUnit 5 + MockMvc/Testcontainers.
- **ASP.NET Core**: minimal APIs or controllers with FluentValidation/DataAnnotations, ProblemDetails, policy-based authorization; xUnit + WebApplicationFactory.
- **Go**: `net/http`/chi, explicit error types, `context` timeouts, table-driven tests.
Use the framework's own DI/abstractions; do not add repository/service/factory layers that only forward calls.

## Implementation guidance
1. Read the contract; implement routes with exactly those paths, methods, status codes and schemas. Contract test: compare responses to the OpenAPI schema (schemathesis / openapi validators) where feasible.
2. Validate input at the boundary (types, ranges, lengths, enums); reject unknown fields to block mass assignment.
3. Authentication: argon2id/bcrypt password hashing, constant-time comparison, short-lived access tokens or server sessions in httpOnly+Secure+SameSite cookies; rotate refresh tokens; lockout/rate limit login.
4. Authorization: check ownership/role on **every** object access server-side (`WHERE owner_id = :user`), not only at route level.
5. Errors: one error schema; 400/422 validation, 401, 403, 404, 409, 429, 500 (no stack traces to clients). Log with a request id.
6. Persistence: parameterised queries/ORM only; explicit transactions around multi-write operations; pagination (`limit`+`cursor` or offset with max) and filtering per contract.
7. Outbound calls (incl. AI service): timeouts, bounded retries with backoff for idempotent operations, circuit-breaking where needed, SSRF protection (allow-list hosts).
8. Background work: only when the architecture defines it; idempotent jobs; no in-request long work beyond timeouts.
9. Logging: structured JSON, levels, request ids; never log passwords, tokens, PII or full prompts.
10. Config: env vars via a settings object (pydantic-settings, `@ConfigurationProperties`, `IOptions`); fail fast when required ones are missing; `.env.example` placeholders only.

## AI boundary
The AI Engineer owns `ai/` modules (prompts, chains/graphs, retrieval, model config) and exposes a typed interface (e.g. `answer_question(user_id, question) -> Answer`). The backend owns HTTP transport, authn/authz, rate limits, persistence of conversations and wiring. Never call model SDKs directly from route handlers.

## Testing
Unit tests for business rules; integration tests through the HTTP layer against a real test database (SQLite/Testcontainers/docker labelled per run); negative tests for authz (user A cannot read user B's data), validation and error mapping; contract tests. Deterministic fixtures, no network to third parties.

## Anti-patterns
Fat controllers with SQL; catching all exceptions and returning 200; `CORS *` with credentials; secrets in code; returning ORM entities with internal fields; N+1 queries; business logic in middleware; tests that only assert status codes.

## Validation
Run lint (ruff/eslint), typecheck (mypy/pyright/tsc where configured), unit+integration tests, build — record exit codes and logs. Exercise one happy and one failure path per endpoint.

## Completion criteria
Endpoints match the contract, authz enforced per object, validation + error contract consistent, tests pass with evidence, no secrets, docs of API behaviour updated in owned paths.

## References
- OWASP API Security Top 10: https://owasp.org/API-Security/
- FastAPI: https://fastapi.tiangolo.com/ · Pydantic: https://docs.pydantic.dev/latest/
- Django: https://docs.djangoproject.com/ · DRF: https://www.django-rest-framework.org/
- Express: https://expressjs.com/ · NestJS: https://docs.nestjs.com/
- Spring Boot: https://docs.spring.io/spring-boot/ · ASP.NET Core: https://learn.microsoft.com/aspnet/core/
- RFC 9457 Problem Details: https://www.rfc-editor.org/rfc/rfc9457
