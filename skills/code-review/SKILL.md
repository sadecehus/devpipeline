---
name: code-review
description: "Independent code review and final acceptance method for DevPipeline: reviewing the integration diff against contracts and requirements, severity/blocking classification, evidence freshness checks, acceptance-matrix construction, and what reviewers must never do."
user-invocable: false
---

# Code review and final acceptance

## Activation criteria
Code Reviewer (balanced/strict) and Final Reviewer (always).

## Required inputs
Integration worktree, base revision, contracts, requirements, ARCHITECTURE.md, check/QA/security/AI reports and gate files in `<run_dir>`, issues, verdict path and current fingerprint.

## Code review procedure
1. `git -C <integration> diff --stat <base>..HEAD`, then read every changed file (prioritise auth, data access, money, concurrency, AI tools).
2. Check, per file: correctness against the AC and contract (status codes, schema fields, error contract); error handling and resource cleanup (files, connections, timers); concurrency (races, missing transactions/locks, async misuse); input validation and authorization; duplication and unnecessary abstraction; dead code and leftover debug; accidental scope expansion (features nobody asked for); test quality (do tests assert behaviour? do they cover failure paths? were tests weakened?).
3. Classify each finding: severity (critical/high/medium/low/info) and `blocking` — blocking only for correctness bugs, security issues, contract/requirement violations, data loss risks or test weakening. Style preferences are non-blocking (or omitted).
4. Each finding: `affected_files`, description with file:line, why it matters, concrete fix, `assigned_agent` = owning engineer (see tasks.json ownership).

## Final review procedure
1. Read the original request and PRD; list every requirement and AC.
2. For each AC decide: VERIFIED (an executed check/observed browser check on the **current** fingerprint covers it — cite the log/report path), IMPLEMENTED_UNVERIFIED (code exists, evidence missing/stale), FAILED (evidence shows failure), MISSING (no implementation), BLOCKED (depends on unavailable credentials/approval).
3. Check evidence freshness: report `source_fingerprint` must equal the fingerprint in your prompt (QA reports use the production-code fingerprint recorded by the gate; check reports the code fingerprint). Stale evidence ⇒ not verified.
4. Check unresolved issues (issues/*.json with status OPEN), failed gates (latest gates/gate-*.json) and architecture compliance.
5. Verdict: `approved: true` only if all must ACs are VERIFIED, no blocking issue is open and no mandatory check failed. Your approval never overrides a failed deterministic check — the gate decides completion together with you.

## Never
Modify code; approve your own work; approve with open blocking findings; mark ACs VERIFIED from code reading alone when the method is a test; quote secrets.

## Output
Review schema (`review.schema.json`): kind, agent, approved, status (APPROVED / REPAIR_REQUIRED / BLOCKED / NEEDS_HUMAN_REVIEW), source_fingerprint, requirements_total/verified, missing_requirements, acceptance_matrix (final review), blocking_issues, non_blocking_issues, required_repairs, evidence, summary.

## Completion criteria
All changed files reviewed (or unreviewed ones listed with NEEDS_HUMAN_REVIEW), every AC has a matrix row with evidence, verdict validates.

## References
- Google engineering practices — code review: https://google.github.io/eng-practices/review/
- OWASP Code Review Guide: https://owasp.org/www-project-code-review-guide/
- Conventional Comments (finding phrasing): https://conventionalcomments.org/
