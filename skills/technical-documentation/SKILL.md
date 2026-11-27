---
name: technical-documentation
description: "Documentation standards for DevPipeline-generated projects: README structure, environment variables without values, verified commands, architecture/API/database/AI/deployment sections, limitations, troubleshooting, completed vs incomplete features, and the deterministic documentation gate."
user-invocable: false
---

# Technical documentation

## Activation criteria
Technical Writer (always at the DOCUMENTING stage); engineers documenting their modules.

## Required inputs
Integration worktree, architecture.json (env_vars, app_start, components), requirements, latest gate result and final review (what is verified), package scripts / Makefile / pyproject.

## README structure
1. Title + one-paragraph purpose (what problem, for whom).
2. Features — split **Verified** (final review VERIFIED), **Implemented but unverified**, **Not implemented / known gaps**.
3. Requirements (runtimes with versions, Docker if needed).
4. Installation (`npm ci` / `uv sync --frozen` …) — copy-pasteable.
5. Configuration — table of env vars: name, required, description, example placeholder; never real values; point to `.env.example`.
6. Running (dev and production-like) — exact commands that exist; ports/URLs.
7. Testing — unit/integration/e2e commands; how to run the AI evaluation suite (and that live evaluation needs credentials and `RUN_LIVE_AI_TESTS=1`).
8. Architecture overview — components, data flow, key decisions (link ARCHITECTURE.md/ADRs if copied into `docs/`).
9. API usage — main endpoints with example requests/responses (from the contract).
10. Database — setup, migrations, seeds, reset for local dev only.
11. AI configuration — providers, model ids via env, limitations, data sent to providers.
12. Deployment — documented steps only (DevPipeline never deploys).
13. Limitations, Troubleshooting (common errors and fixes), License.
A short Turkish summary section is welcome when the original request was Turkish.

## Verification rules
- Every command you document must exist: grep `package.json` scripts, Makefile targets, pyproject scripts. The documentation gate fails when README references `npm run X`/`pnpm X`/`yarn X` that no package.json defines.
- Every architecture env var must appear in README or `.env.example`.
- Run safe commands where practical (`--help`, install, test) and record exit codes in your result.
- Do not change code/config to match the docs — document reality; raise issues for mismatches.
- Only documentation files may change (`*.md`, `docs/`, `.env.example`, `LICENSE`); other edits are flagged as ownership violations.

## Style
Imperative, concise, task-oriented headings; code blocks with language tags; relative links; no marketing language; no secrets, internal hostnames or personal data.

## Anti-patterns
Documenting planned features as done; placeholder sections ("TBD"); commands that don't exist; copying the PRD instead of explaining usage; secret values in examples.

## Completion criteria
README contains installation, running and testing sections plus the structure above, all commands exist, env vars documented without values; documentation gate passes.

## References
- Diátaxis documentation framework: https://diataxis.fr/
- Make a README: https://www.makeareadme.com/
- Google developer documentation style guide: https://developers.google.com/style

## Example (configuration table)
| Variable | Required | Description | Example |
|---|---|---|---|
| `DATABASE_URL` | yes | PostgreSQL connection string | `postgresql://app:app@localhost:5432/app_dev` |
| `ANTHROPIC_API_KEY` | for live AI only | model provider key — keep in `.env`, never commit | *(empty)* |
| `RUN_LIVE_AI_TESTS` | no | set to `1` to run live model evaluations | `0` |
