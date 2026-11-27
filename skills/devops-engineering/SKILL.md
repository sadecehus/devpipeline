---
name: devops-engineering
description: "Reproducible environments for DevPipeline projects: minimal non-root Dockerfiles, Docker Compose for local dependencies, env templates, CI pipelines without deploy steps, health checks, startup scripts, lockfile installs and resource cleanup — never deploying or creating paid resources automatically."
user-invocable: false
---

# DevOps engineering

## Activation criteria
DevOps Engineer tasks (features.devops, containers or CI required); integration engineer starting environments.

## Required inputs
architecture.json (components, env_vars, app_start, ports), stack/lockfiles, task owned paths.

## Dockerfiles
- Multi-stage: builder with dev deps → runtime with prod deps only; pin base image tags (`node:22-bookworm-slim`, `python:3.12-slim`), avoid `latest`.
- Non-root user (`USER node` / create `appuser`), `WORKDIR /app`, copy lockfiles first for layer caching, `npm ci --omit=dev` / `uv sync --frozen --no-dev`, `HEALTHCHECK`, `EXPOSE` only the app port.
- `.dockerignore` (node_modules, .git, .env*, .devpipeline, tests artifacts). No secrets via `ARG`/`ENV`; use runtime env or secrets mounts.

## Docker Compose (local dependencies)
Services named after the project; ports bound to `127.0.0.1:<port>:<port>`; named volumes; healthchecks with `depends_on: condition: service_healthy`; env from `.env` (git-ignored) with `.env.example` committed. For pipeline-created test containers use names/labels containing the run id (`devpipeline-<run>-db`) and stop/remove them afterwards; never touch other containers or volumes.

## Env templates
`.env.example` lists every variable from architecture.env_vars with a comment and placeholder (`DATABASE_URL=postgresql://app:app@localhost:5432/app_dev`, `ANTHROPIC_API_KEY=` empty). Separate dev/test/prod settings; prod values never in the repo.

## CI (e.g. GitHub Actions)
Jobs: install from lockfile with caching → lint → typecheck → unit/integration tests (service containers for DBs) → build → optional e2e. Pin action versions (major tags or SHAs), least-privilege `permissions:`, no deploy/publish steps, no secrets echoed. CI files are created, never executed against remote infrastructure by the pipeline.

## Startup & health
Provide one documented command per environment (`make dev`, `docker compose up -d db && npm run dev`), a `/health` (liveness) and optionally `/ready` endpoint wired with the backend owner, graceful shutdown (SIGTERM).

## Prohibited
Deploying (vercel/fly/k8s/terraform apply), pushing images, creating cloud resources or DNS, pruning Docker resources, editing system configuration. Document deployment steps in README instead.

## Validation
`docker compose config -q`, `docker build` of changed images (if Docker available; else NOT_RUN), `hadolint` if installed, `actionlint` for workflows if installed; start → healthcheck → stop for compose stacks you created.

## Completion criteria
Environment starts with the documented command (or the limitation is evidenced), images minimal/non-root, `.env.example` complete, CI valid and deploy-free, created resources cleaned up.

## References
- Docker best practices: https://docs.docker.com/build/building/best-practices/
- Compose spec: https://docs.docker.com/compose/compose-file/
- GitHub Actions security hardening: https://docs.github.com/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions
- Twelve-Factor: https://12factor.net/ · hadolint: https://github.com/hadolint/hadolint

## Example (compose fragment for a local PostgreSQL)
```yaml
services:
  db:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app        # local development only; never reuse elsewhere
      POSTGRES_DB: app_dev
    ports: ["127.0.0.1:5432:5432"]
    volumes: [db-data:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d app_dev"]
      interval: 5s
      retries: 10
volumes:
  db-data: {}
```
