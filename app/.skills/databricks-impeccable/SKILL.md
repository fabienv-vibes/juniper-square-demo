---
name: databricks-impeccable
description: "Create production-grade frontend UIs, PDFs, slides, and apps that conform to the Databricks visual identity. Locks the aesthetic to Lava 600 #FF3621, Navy 900 #0B2026, Light #F9F7F4, DM Sans typography, and Du Bois components. Use when building any Databricks-facing UI, internal tool, demo, or customer-facing artifact. Call with 'craft' for shape-then-build, 'teach' for one-time context setup, or 'extract' to pull reusable tokens/components into the design system."
argument-hint: "[craft|teach|extract]"
user-invocable: true
license: Apache 2.0. Based on impeccable v2.1.7 by Paul Bakaus. See NOTICE.md.
---

This skill produces production-grade Databricks-branded interfaces. The aesthetic is **pre-committed** to the Databricks visual identity — do not re-litigate brand choices per project. Your job is to execute the brand with precision, not to invent a new one.

## Context Gathering Protocol

Design skills produce generic output without project context. You MUST have confirmed design context before doing any design work.

**Required context** — every Databricks surface needs:

- **Target audience**: Internal Databricks employees, external customers, partner-facing, or field demo?
- **Surface type**: Databricks App (Streamlit / Dash / FastAPI+React), marketing demo, internal SA tool, AI/BI dashboard, PDF report, slide deck, or reveal.js?
- **Use case**: What job is the user trying to get done?
- **Light or dark theme**: Derive from audience and viewing context (see `reference/color-and-contrast.md`).

The **brand** is already decided — do not ask about colors, typography, or personality. Those are fixed.

**Gathering order**:

1. **Check current instructions** — if a `## Design Context` section exists in the loaded instructions, proceed immediately.
2. **Check `.databricks-impeccable.md`** — if the project root has one with the required context, proceed.
3. **Run `/databricks-impeccable:teach`** — required if neither source has context. Do NOT infer from the codebase.

## The aesthetic is locked

Do not pick a "direction." The direction is:

- **Editorial-plain** — confident, declarative, unadorned. Headers are labels, not taglines.
- **Palette**: Light `#F9F7F4` surface, Navy 900 `#0B2026` text, **Lava 600 `#FF3621` as 10% accent only**.
- **Type**: DM Sans 400/500/700 + DM Mono 400/500. No substitutes.
- **Spacing**: 8pt grid via `--db-space-*` tokens.
- **Components**: Du Bois first. Custom only when Du Bois doesn't ship it.
- **Voice**: No superlatives, no slide-deck framing, plain statements. See `reference/ux-writing.md`.

Full rules: `reference/brand-rules.md`. All CSS tokens: `reference/brand-tokens.css`.

## Frontend aesthetics guidelines

### Typography
→ *See `reference/typography.md` for the full type scale and OpenType features.*

Locked: DM Sans + DM Mono. Use the `--db-text-*` scale. Body at 16 px minimum. Line length ≤ 65-75ch.

**DO**: DM Sans 400/500/700. Semantic tokens. `rem` not `px`. `font-display: swap`.
**DO NOT**: Substitute another font — ever. Set body < 16 px. Use 5+ weights. Decorative/display fonts.

### Color & theme
→ *See `reference/color-and-contrast.md` for the full palette, WCAG table, and dark-mode scaffold.*

Palette is fixed. **60% neutrals / 30% text+borders / 10% Lava 600 max.** Audit will flag viewports where orange exceeds 10%.

**DO**: Use `--db-*` tokens. Tint neutrals warm (already baked in). Check WCAG for every pair.
**DO NOT**: Introduce colors outside `brand-tokens.json`. Use `#FF3621` as body-text color on white (fails WCAG AA). Default dark mode with glowing accents (AI slop).

### Layout & space
→ *See `reference/spatial-design.md` for grids, container queries, and touch targets.*

8pt grid. `gap` not margins. Container queries for components, viewport queries for page shell.

**DO**: Self-adjusting grids (`repeat(auto-fit, minmax(280px, 1fr))`). Vary spacing by hierarchy. Left-aligned asymmetric layouts.
**DO NOT**: Wrap everything in cards. Nest cards in cards. Center everything. Let body wrap past 80 chars.

### Visual details — the bans

These patterns are never acceptable. Match-and-refuse — if you catch yourself about to write any of these, rewrite the element with a different structure.

**BAN 1 — side-stripe borders**
`border-left` or `border-right` > 1 px on cards/list items/alerts/callouts. Hard-coded color or CSS variable. The single most overused AI design tell. Rewrite with full borders, background tints, leading numbers/icons, or no visual indicator.

**BAN 2 — gradient text**
`background-clip: text` combined with any gradient. Solid colors only for text. Use weight or size for emphasis.

**BAN 3 — orange overuse**
Lava 600 above 10% of the viewport. This includes CTAs, top bars, status dots, link underlines, icons — all orange pixels add up. Pull back.

**BAN 4 — wrong font**
Anything that isn't DM Sans / DM Mono. Rewrite using `var(--db-font-primary)`.

**BAN 5 — purple-to-blue gradient, glassmorphism default, hero-metric template, bounce easing, sparkline decoration**
The full AI slop inventory. Always reject.

### Motion
→ *See `reference/motion-design.md`.*

Exponential easing (`--db-ease-quart/quint/expo`). Only animate `transform` and `opacity`. Respect `prefers-reduced-motion`.

### Interaction
→ *See `reference/interaction-design.md`.*

Handle all eight states. `:focus-visible`. One primary button per screen. Du Bois first.

### Responsive
→ *See `reference/responsive-design.md`.*

Mobile-first. Container queries for components. 44 × 44 px touch targets. Adapt, don't amputate.

### UX writing
→ *See `reference/ux-writing.md`.*

Plain, active, specific. No superlatives. Sentence case for headings. Imperative verbs for actions.

## The AI Slop test

Final quality check before delivering: if someone saw this and said "AI made this," would they believe you immediately? If yes, that's the problem.

For Databricks specifically: a stranger should recognize this as a Databricks-made artifact **without a logo**. If it could be any SaaS company's UI — reshape it until the palette, typography, and voice make it unmistakable.

## Craft mode

If invoked as `/databricks-impeccable:databricks-impeccable craft [description]`, follow `reference/craft.md`.

## Teach mode

If invoked as `/databricks-impeccable:databricks-impeccable teach`, skip all design work and run the teach flow below.

### Step 1 — scan the codebase

- README and docs for audience and purpose.
- `package.json` and config for tech stack.
- Existing components, CSS tokens, and any `brand-tokens.css` present.
- Databricks-specific hooks: `databricks.yml` for Asset Bundles, `app.yaml` for Apps, `.databrickscfg` for profiles.

### Step 2 — ask **only** what you can't infer

The brand doesn't need discovery. Ask:

- Surface type (App / internal tool / demo / dashboard / PDF / slides)?
- Audience (internal Databricks / customer / partner / field)?
- Light, dark, or both?
- Use case summary in one sentence?
- Any accessibility requirements beyond WCAG AA?

Do **not** ask about colors, fonts, personality, or tone — those are decided.

### Step 3 — write Design Context

Write a `## Design Context` section to `.databricks-impeccable.md` in the project root:

```markdown
## Design Context

### Surface type
[App / demo / dashboard / PDF / slides]

### Audience
[Internal / customer / partner / field]

### Theme
[light / dark / both]

### Use case
[One sentence]

### Brand (locked)
- Palette: Lava 600 #FF3621 (10% accent), Navy 900 #0B2026, Light #F9F7F4
- Typography: DM Sans 400/500/700 + DM Mono
- Components: Du Bois first
- Voice: Databricks editorial-plain (no superlatives, plain statements)

### Additional constraints
[Performance, offline support, browser matrix, accessibility beyond WCAG AA — only if specified]
```

Ask if the user also wants this appended to `CLAUDE.md`. If yes, append.

## Extract mode

If invoked as `/databricks-impeccable:databricks-impeccable extract [target]`, follow `reference/extract.md`.
