# Design system — {{project_title}}

## 0. Inputs and tooling
- UI UX Pro Max queries run: `{{search.py "<keywords>" --design-system -p "<Project>"}}` → {{summary of recommendations used}}
- (If unavailable: state it here and why.)

## 1. Product identity
{{domain, audience, tone; what makes this UI specific (not generic)}}

## 2. Tokens
| Token | Value | Usage |
|---|---|---|
| `--color-bg` | | page background |
| `--color-fg` | | body text (contrast vs bg: x.x:1) |
| `--color-primary` | | primary actions |
| `--space-1..8` | 4/8 px scale | spacing |
| `--radius-sm/md` | | |
| `--font-sans` / `--font-mono` | | |
| type scale | | h1…body…caption |

## 3. Layout & breakpoints
Mobile ≈390 px, tablet ≈768 px, desktop ≈1440 px; grid, max widths, navigation pattern per breakpoint.

## 4. Component inventory
| Component | Variants | States (default/hover/focus-visible/active/disabled/loading/error/empty/success) | Notes |
|---|---|---|---|

## 5. Page specifications
### {{Page}}
- Purpose / primary action
- Layout (desktop / mobile)
- Data states: loading · empty · error · success
- Interactions

## 6. Accessibility checklist (WCAG 2.2 AA)
- [ ] Text contrast ≥ 4.5:1, large text/UI ≥ 3:1
- [ ] Visible focus on all interactive elements
- [ ] Keyboard operable, no traps, logical order
- [ ] Labels and accessible names; errors announced
- [ ] Target size ≥ 24×24 px
- [ ] Reduced motion respected
- [ ] Landmarks and heading hierarchy

## 7. Motion
{{durations, easing; only purposeful}}
