---
name: audit
description: "Run technical quality and brand-compliance checks on a Databricks interface. Scores accessibility, performance, theming, responsive, anti-patterns, and Databricks brand conformance (palette, typography, orange usage, Du Bois coverage) with P0-P3 severity. Generates a report; does not fix."
argument-hint: "[area (feature, page, component...)]"
user-invocable: true
---

## MANDATORY PREPARATION

Invoke `/databricks-impeccable:databricks-impeccable` — it contains the brand rules and AI-slop bans. If no Design Context exists, run `/databricks-impeccable:databricks-impeccable teach` first.

---

Run systematic technical + brand checks. Document findings, do not fix them. Other skills (`polish`, `colorize`, `typeset`, etc.) handle fixes.

## Diagnostic scan

Six dimensions, scored 0-4 each. Max 24.

### 1. Accessibility

- Contrast < 4.5:1 for body, < 3:1 for UI components.
- Missing ARIA roles/labels/states on interactive elements.
- Keyboard nav failures: missing focus indicators, illogical tab order, traps.
- Semantic HTML: heading hierarchy, landmarks, `<button>` vs `<div>`.
- Form issues: inputs without `<label>`, poor error messaging, missing required indicators.

**Score**: 0 fails WCAG A · 1 major gaps · 2 partial · 3 AA mostly met · 4 AA fully met.

### 2. Performance

- Animating layout properties (height/width/padding/margin).
- Missing lazy-loading on images.
- Large bundle / unused imports.
- Unnecessary re-renders, missing memoization.

**Score**: 0 severe · 1 major · 2 partial · 3 good · 4 fast and lean.

### 3. Theming

- Hard-coded colors not using `--db-*` tokens.
- Missing dark-mode variants.
- Inconsistent token usage.

**Score**: 0 no tokens · 1 minimal · 2 partial · 3 mostly tokenized · 4 full token coverage.

### 4. Responsive

- Fixed widths that break on mobile.
- Touch targets < 44 × 44 px.
- Horizontal scroll on narrow viewports.
- Text that breaks at 200% zoom.

**Score**: 0 desktop-only · 1 major issues · 2 partial · 3 good · 4 excellent.

### 5. Anti-patterns (AI slop)

Check against every ban in `brand-rules.md`:

- `border-left` / `border-right` > 1 px accent stripes.
- Gradient text (`background-clip: text`).
- Glassmorphism as default.
- Hero metric template.
- Bounce/elastic easing.
- Purple-to-blue gradients.
- Sparkline decoration.
- Gray text on colored backgrounds.

**Score**: 0 slop gallery (5+) · 1 heavy (3-4) · 2 some (1-2) · 3 mostly clean · 4 distinctive.

### 6. Databricks brand compliance — **CRITICAL**

- **Font**: DM Sans loaded and applied everywhere? DM Mono for code? (P0 if substitute font is used.)
- **Palette**: every color from `brand-tokens.json`? (P1 for any color outside the palette.)
- **Lava 600 usage**: ≤ 10% of viewport? Measure by screenshotting and pixel-sampling, or by counting orange-colored elements relative to total. (P1 if > 10%.)
- **Logo**: used correctly? Clear space respected? Not rotated, recolored, or redrawn? (P0 if logo is recreated as SVG rather than imported.)
- **Du Bois coverage**: are ad-hoc buttons/inputs/tables present where Du Bois ships a primitive? (P2 for each.)
- **Voice**: banned words present in UI copy (pivotal, leverage, seamless, unlock, transform, unleash, powerful, robust, best-in-class)? (P2 for each.)
- **8pt grid**: spacing values on the grid? (P2 for off-grid values without a documented reason.)

**Score**: 0 off-brand (wrong font, wrong palette, logo misused) · 1 multiple violations · 2 some violations · 3 mostly compliant · 4 fully Databricks.

## Generate report

### Audit Health Score

| # | Dimension | Score | Key finding |
|---|---|---|---|
| 1 | Accessibility | ? | |
| 2 | Performance | ? | |
| 3 | Theming | ? | |
| 4 | Responsive | ? | |
| 5 | Anti-patterns | ? | |
| 6 | **Databricks brand** | ? | |
| **Total** | | **?? / 24** | |

**Rating bands**: 22-24 excellent (ship) · 18-21 good (minor polish) · 14-17 acceptable (real work needed) · 10-13 poor (major overhaul) · 0-9 critical (restart).

### Anti-patterns verdict

Pass / fail: would a stranger recognize this as AI-generated? Would they recognize it as Databricks without seeing a logo? List specific tells.

### Brand compliance verdict — **lead with this**

Pass / fail on the four hardlines:

1. DM Sans loaded? ✓ / ✗
2. Every color in `brand-tokens.json`? ✓ / ✗
3. Orange usage ≤ 10%? ✓ / ✗
4. Logo used, not recreated? ✓ / ✗

Each ✗ is at minimum P1.

### Detailed findings

Tag every issue:

- **P0 Blocking** — fix immediately.
- **P1 Major** — WCAG AA violation, brand hardline failure, fix before release.
- **P2 Minor** — inconsistency, minor off-brand, fix next pass.
- **P3 Polish** — nice-to-fix, no user impact.

Each finding documents: location, category, impact, standard violated (if any), recommendation, which skill to invoke to fix.

### Recommended actions

Ordered list of skills to run (P0 first). End with `/databricks-impeccable:polish` as the final pass.

## Rules

**Never**:
- Skip the Databricks brand dimension.
- Over-report P3 — too many creates noise.
- Recommend a skill that isn't in this plugin.
- Fix anything in this skill — audit only reports.

**Always**:
- Score every dimension, even if 0.
- Lead with brand compliance and anti-pattern verdict.
- Cite specific file paths and line numbers.
- Close with a prioritized action list.
