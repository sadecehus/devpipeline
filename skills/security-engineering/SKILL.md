---
name: security-engineering
description: "Application security review for DevPipeline: OWASP Top 10, ASVS and API Top 10 checklists, threat modelling, authN/authZ, injection/XSS/CSRF/SSRF/path traversal/uploads/deserialization, secrets and logging, dependency and secret scanning (semgrep, bandit, gitleaks, trivy, npm audit, pip-audit, osv-scanner), LLM-specific risks, finding format and severity."
user-invocable: false
---

# Security engineering

## Activation criteria
Security Engineer (balanced/strict profiles, auth/payment/AI projects); Backend/AI engineers implementing auth or tools; reviewers.

## Required inputs
Integration worktree, ARCHITECTURE.md (trust boundaries, auth design), env var list, dependency manifests/lockfiles, report path and fingerprint.

## Method
1. **Threat model (lightweight STRIDE)** — list assets (user data, credentials, model keys), entry points (HTTP routes, uploads, webhooks, AI tools), trust boundaries; note top threats in the report summary.
2. **Manual review checklist**
   - AuthN: password hashing (argon2id/bcrypt), session/token lifetime, cookie flags (HttpOnly, Secure, SameSite), logout invalidation, brute-force protection, MFA if required.
   - AuthZ: object-level checks on every read/write (IDOR), function-level checks, tenant isolation, no client-side-only checks.
   - Injection: parameterised SQL/ORM, no shell with user input, NoSQL operator injection, template injection, LDAP/XPath.
   - XSS: output encoding, no unsafe HTML sinks with untrusted data, CSP where appropriate.
   - CSRF: SameSite cookies + token for cookie-authenticated state changes.
   - SSRF: outbound URL allow-lists, block internal ranges/metadata IPs.
   - Files: upload type/size validation by content, stored outside webroot, random names, path traversal (`..`, absolute paths, symlinks) on every file path built from input.
   - Deserialization: no pickle/yaml.load/BinaryFormatter on untrusted data.
   - Data exposure: minimal response fields, no secrets/PII in logs or errors, encryption in transit, sensible security headers (HSTS, X-Content-Type-Options, frame-ancestors).
   - Secrets: none in code, configs, tests, docs or git history; `.env` ignored; `.env.example` placeholders only.
   - Rate limiting and resource limits (body size, pagination caps, timeouts).
   - **LLM/AI**: prompt injection paths (user input, retrieved docs, tool outputs), tool allow-lists and authorization outside the model, output handling (no execution/HTML injection), data sent to providers, denial-of-wallet (rate limits, max tokens), model output schema validation.
3. **Scanners** — run only those installed and relevant; record each in `scanners[]` with `available`/`ran`/`status`: `semgrep scan --config auto --error` (needs network for rules; else `--config p/<lang>` offline packs if present), `bandit -r <pkg>`, `gitleaks detect --no-banner --redact`, `trivy fs --scanners vuln,secret,misconfig .`, `npm audit --omit=dev --audit-level=high` (pnpm: `pnpm audit --prod --audit-level high`), `pip-audit -r requirements.txt` (or `pip-audit .`; never bare `pip-audit`, which scans the whole interpreter), `osv-scanner -r .`. The dependency-audit check reflects **production** dependencies; advisories only in dev tooling are reported as separate non-blocking `low` issues, not as a FAIL check (a mandatory gate must be fixable). Every executed scanner records `command`, `exit_code` and a `log_path`. Unavailable mandatory scanner = NOT_RUN (never PASS). Do not install scanners system-wide without approval.
4. **Findings** — issue schema: id, `severity` (critical: remote exploit/auth bypass/secret leak; high: injection/IDOR with impact; medium: missing hardening with plausible impact; low/info), `category: security|ai_security`, `affected_files` (file:line in description), evidence (scanner output path or code reasoning), `impact`, `remediation`, `assigned_agent` (owner of the code), `verification_criteria`, `blocking` (critical/high always true).

## Scope rules
Only the current project and approved local environments. No external scanning, fuzzing of third-party services, credential stuffing or exploitation beyond a minimal local reproduction. Never print discovered secrets — reference location and redact.

## Anti-patterns
Speculative findings without code paths; severity inflation; treating scanner output as complete; marking unavailable scanners as passed; fixing code yourself instead of routing.

## Completion criteria
Checklist reviewed, scanner availability recorded, each finding actionable and routed, report validates; unresolved critical/high block completion.

## References
- OWASP Top 10: https://owasp.org/Top10/ · ASVS: https://owasp.org/www-project-application-security-verification-standard/
- OWASP API Security Top 10: https://owasp.org/API-Security/ · Cheat Sheets: https://cheatsheetseries.owasp.org/
- OWASP Top 10 for LLM Apps: https://genai.owasp.org/llm-top-10/
- Semgrep: https://semgrep.dev/docs/ · Bandit: https://bandit.readthedocs.io/ · Gitleaks: https://github.com/gitleaks/gitleaks
- Trivy: https://trivy.dev/ · pip-audit: https://pypi.org/project/pip-audit/ · OSV-Scanner: https://google.github.io/osv-scanner/
