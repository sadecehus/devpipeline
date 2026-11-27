---
name: browser-testing
description: "Browser QA procedure: verifying Playwright MCP availability in the agent's own context, starting the app locally, exploratory journeys across 390/768/1440 viewports, console/network/accessibility inspection, screenshots, and converting findings into repeatable Playwright tests run by the deterministic runner."
user-invocable: false
---

# Browser testing

## Activation criteria
Browser QA Engineer for any project with a browser UI; Frontend Engineer for a final render check.

## Required inputs
Integration worktree, `architecture.json.app_start` (command, cwd, url, healthcheck), requirements/ACs with `e2e` verification, viewports, report path and fingerprint.

## Procedure
1. **Tool check** — use ToolSearch for `mcp__plugin_devpipeline_playwright__` (tools such as `browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_resize`, `browser_console_messages`, `browser_network_requests`, `browser_take_screenshot`). Record in the report `scanners`: `{"name": "playwright-mcp", "available": true|false, "ran": true|false}`. MCP access of the parent session is not guaranteed for subagents — verify, don't assume.
2. **Environment** — install deps if needed (lockfile install), start the app per `app_start` in the background bound to localhost, wait for the healthcheck/URL (poll up to ~60 s), use test data/seed and deterministic test accounts from env or fixtures. Only `localhost`/`127.0.0.1` or explicitly approved staging URLs.
3. **Exploratory pass** (Playwright MCP) for every critical journey mapped to ACs: navigation, forms (valid + invalid), buttons, auth flows (login/logout, protected routes), validation messages, error/empty/loading states, at each viewport (`browser_resize` 390×844, 768×1024, 1440×900). After each journey read console messages and failed network requests. Take screenshots of failures into `<run_dir>/screenshots/<check>-<viewport>.png`.
4. **Accessibility pass** — accessible names in the snapshot tree, focus order via keyboard (`Tab`), visible focus, form labels, heading structure; if `@axe-core/playwright` is in the project, run it in the suite.
5. **Automate** — write Playwright tests (`e2e/*.spec.ts`) for every verified journey: `getByRole/getByLabel` locators, `expect(...).toBeVisible()` web-first assertions, no fixed timeouts, one test per AC (title contains the AC id), projects for desktop + mobile viewports, `webServer` in `playwright.config.ts` to start the app, `trace: 'retain-on-failure'`. Ensure `package.json` has `"test:e2e": "playwright test"` (ask the owner via an issue if you cannot edit package.json). Install browsers with `npx playwright install chromium` when missing.
6. **Run** the suite yourself, attach exit code/log; the deterministic runner re-runs it as the `e2e` check.
7. **Report** (kind `browser_qa`): one check per journey×viewport with `evidence_type` `observed` (MCP) or `executed` (suite), expected vs actual, reproduction steps, screenshots; issues to frontend/backend/devops owners. Stop the app.

## Safety
Never test unrelated or production systems; never submit real payments, send real email/SMS/messages or call real third-party side-effect APIs (use test modes or mocks at the network boundary); no real credentials in tests or traces.

## Anti-patterns
Approving a web app never started in a browser; screenshots without assertions; CSS/XPath selectors tied to styling; `waitForTimeout`; tests depending on each other's data; exploratory-only evidence without a repeatable suite.

## Completion criteria
App started and exercised in a real browser (or the reason it could not be is recorded as NOT_RUN/FAIL), every e2e AC has an executed or observed check, a repeatable Playwright suite exists and passes or failures are routed.

## References
- Playwright MCP: https://github.com/microsoft/playwright-mcp
- Playwright docs: https://playwright.dev/docs/intro · Best practices: https://playwright.dev/docs/best-practices
- axe-core Playwright: https://playwright.dev/docs/accessibility-testing
- Chrome DevTools MCP (optional debugging): https://github.com/ChromeDevTools/chrome-devtools-mcp
