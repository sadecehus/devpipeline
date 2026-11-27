---
name: frontend-engineering
description: "Frontend implementation standards for DevPipeline: stack defaults (Next.js/TypeScript/Tailwind/shadcn/ui/Lucide) and alternatives, component architecture, design-token usage, forms and validation, data fetching against contracts, accessibility (WCAG 2.2 AA), performance and component testing. Also contains the anti-generic design rules used when UI UX Pro Max is unavailable."
user-invocable: false
---

# Frontend engineering

## Activation criteria
Frontend Engineer and UI/UX Designer tasks; Browser QA when judging UI behaviour.

## Required inputs
Task entry (owned paths, ACs), DESIGN_SYSTEM.md (tokens, components, page specs), contracts (`openapi.yaml`/shared types), architecture `stack`.

## Stack decision tree
- Stack given by architecture → use exactly that (React/Vite, Next.js, Vue/Nuxt, Svelte/SvelteKit, Angular). Never add a second framework.
- New general web app → Next.js (App Router) + TypeScript strict + Tailwind CSS + shadcn/ui + Lucide icons.
- Content-heavy/SEO → server components and static rendering; dashboards behind auth → server components for data, client components for interactivity only.
- State: URL/search params and server data first; React context for small shared UI state; a store library (Zustand/Redux) only when several distant components mutate shared client state.
- Data fetching: server components / route loaders where available; on the client use the framework's primitives or TanStack Query; generate types from the OpenAPI contract (`openapi-typescript`) instead of hand-writing them.

## Implementation guidance
- **Tokens:** consume CSS variables / Tailwind theme from the design system; no raw hex values or arbitrary spacing in components.
- **Components:** one responsibility each; colocate tests (`Component.test.tsx`); export from feature folders; reuse shadcn/ui primitives instead of re-implementing dialogs, menus, comboboxes.
- **States:** every data view implements loading (skeleton matching layout), empty (explanation + primary action), error (message + retry), success.
- **Forms:** react-hook-form + zod (or framework equivalent); mirror server validation messages; disable submit while pending; show field-level errors linked with `aria-describedby`; never trust client validation for security.
- **API errors:** map the contract's error schema to user messages; handle 401 (re-auth), 403 (no access UI), 404, 409, 422, network failure and timeouts.
- **Auth UI:** tokens in httpOnly cookies set by the backend; never store tokens in localStorage; route guards are UX only — the server enforces authorization.
- **Env vars:** only public-prefixed variables (`NEXT_PUBLIC_*`, `VITE_*`) reach the browser; never expose secrets.
- **Responsive:** design mobile-first; verify ≈390 / 768 / 1440 px; no horizontal scroll; tap targets ≥ 24×24 px (44 recommended).

## Accessibility checklist (WCAG 2.2 AA)
Semantic landmarks (`header`, `nav`, `main`), one `h1` per page, labelled inputs, visible focus (`:focus-visible`), keyboard operability for all controls, no keyboard traps, contrast ≥ 4.5:1 text / 3:1 UI, `prefers-reduced-motion` respected, images with meaningful `alt`, live regions for async status, focus moved into dialogs and restored on close, target size ≥ 24 px (SC 2.5.8).

## Anti-generic design rules (defaults to avoid)
No purple/indigo gradients by default, no glassmorphism stacks, no oversized rounded cards everywhere, no decorative badges, no random shadows, no emoji icons, no generic hero + three feature cards landing page, no gratuitous animation, no grids of identical KPI cards. Prefer: strong typographic hierarchy, restrained palette with semantic roles, density appropriate to the domain, real content structure. Use any of the above only when the product's design language justifies it.

## Performance
Avoid unnecessary client components; lazy-load heavy, below-the-fold widgets; `next/image` or responsive `srcset`; no large libraries for small utilities (date-fns over moment, native `Intl`); keep route bundles small; avoid request waterfalls (parallel fetch, preload).

## Testing
Vitest/Jest + Testing Library: test behaviour via roles/labels (`getByRole('button', {name: /save/i})`), not implementation details; mock the network at the boundary (MSW) using contract shapes; cover empty/error states and form validation. E2E belongs to Browser QA (Playwright) but keep selectors accessible so their tests are stable.

## Anti-patterns
`any` types, `useEffect` for derived state, fetching in effects without cancellation, prop drilling through 5+ levels instead of composition, duplicated components, inline styles bypassing tokens, `dangerouslySetInnerHTML` with untrusted content, silencing TypeScript or ESLint errors.

## Validation
`<pm> run lint`, `<pm> run typecheck` (or `tsc --noEmit`), `<pm> run test`, `<pm> run build` — only scripts that exist; record exit codes. Start the dev server and check the pages render (Playwright MCP if available).

## Completion criteria
Spec'd pages/components implemented with all states, typed against contracts, accessible, tests and build passing, no secrets or mock data in production paths.

## References
- Next.js docs: https://nextjs.org/docs · React: https://react.dev/reference/react
- Tailwind CSS: https://tailwindcss.com/docs · shadcn/ui: https://ui.shadcn.com/docs · Lucide: https://lucide.dev/guide/
- WCAG 2.2: https://www.w3.org/TR/WCAG22/ · WAI-ARIA APG: https://www.w3.org/WAI/ARIA/apg/
- Testing Library: https://testing-library.com/docs/ · Vitest: https://vitest.dev/guide/
- Use Context7 (`mcp__plugin_devpipeline_context7__*`) for version-specific API details.
