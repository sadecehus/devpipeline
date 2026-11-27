---
name: performance-engineering
description: "Local, bounded performance measurement for DevPipeline: budgets from requirements, Lighthouse/Web Vitals lab runs, bundle analysis, API latency with k6/Locust/timed loops, database EXPLAIN, AI call count/latency, memory checks, repeat-and-median reporting and lab-vs-production honesty."
user-invocable: false
---

# Performance engineering

## Activation criteria
Performance Engineer when requirements contain performance ACs or the strict profile is active.

## Required inputs
Performance requirements (latency, throughput, page load, bundle size), app_start, hot paths from architecture, report path/fingerprint.

## Budgets (defaults when the strict profile has no explicit numbers)
- Web (lab, mobile emulation): LCP ≤ 2.5 s, CLS ≤ 0.1, TBT ≤ 200 ms (INP proxy), JS per route ≤ 200 KB gzip.
- API (local, warm): p95 ≤ 300 ms for CRUD endpoints at 10 concurrent users; zero 5xx.
- DB: no sequential scans on tables expected > 10k rows in hot queries; no N+1.
- AI: count model calls per user action (target = design value), p95 latency recorded; caching only where semantically safe.

## Procedure
1. Start the app in production mode locally (`npm run build && npm start`, `uvicorn` without reload) with seed data.
2. Frontend: `npx lighthouse <url> --preset=desktop|--form-factor=mobile --output=json --quiet --chrome-flags="--headless"` (if available) or Playwright `performance.getEntriesByType('navigation')` + PerformanceObserver for LCP/CLS; bundle size from the build output or `source-map-explorer`/`next build` stats.
3. API: short k6 script (`k6 run --vus 10 --duration 30s script.js`) or Locust headless (`locust -f f.py --headless -u 10 -r 5 -t 30s`), or a timed loop when neither is installed; warm up first.
4. DB: `EXPLAIN (ANALYZE, BUFFERS)` on hot queries against the test DB; check index usage and row estimates.
5. AI: instrument/inspect call counts per request via logs or the fake client counters; measure latency with the fake model (overhead) and live only if allowed.
6. Repeat each measurement ≥ 3×, report median (and p95 for load), machine and mode.

## Reporting
Report kind `performance`: one check per budget with measured value, budget, command, exit code, log path; `evidence_type: executed`; clearly label "lab measurement on a developer machine — not production performance". Issues for violations with the measurement and the likely cause, routed to the owner.

## Rules
Local and short runs only; never load-test external or third-party services; no premature optimisation; no code changes by this role.

## Anti-patterns
Single-run numbers; measuring dev mode; comparing against no budget; optimising without profiling evidence; reporting lab numbers as user experience.

## Completion criteria
Every applicable budget measured with evidence and compared; violations routed; report validates.

## References
- web.dev Core Web Vitals: https://web.dev/articles/vitals · Lighthouse CI: https://github.com/GoogleChrome/lighthouse-ci
- k6: https://grafana.com/docs/k6/latest/ · Locust: https://docs.locust.io/
- PostgreSQL EXPLAIN: https://www.postgresql.org/docs/current/using-explain.html

## Example (k6 smoke load, local only)
```js
import http from 'k6/http'
import { check } from 'k6'
export const options = { vus: 10, duration: '30s', thresholds: { http_req_duration: ['p(95)<300'], http_req_failed: ['rate==0'] } }
export default function () {
  const res = http.get('http://127.0.0.1:8000/api/tasks')
  check(res, { 'status 200': r => r.status === 200 })
}
```
Run: `k6 run perf/tasks.js > <run_dir>/logs/perf-tasks.log` — the thresholds make k6 exit non-zero on a budget violation.
