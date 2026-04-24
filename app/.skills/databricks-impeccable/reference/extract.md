# Extract flow

Pull reusable tokens and components out of an existing codebase into a design system that matches Databricks conventions.

## Prerequisites

Design Context exists. You're working on a real codebase, not a greenfield scaffold.

## Step 1 — Inventory

Scan the codebase for:

- **Raw colors** — grep for hex codes, rgb/hsl strings, Tailwind color classes. Record counts.
- **Raw pixel values** — grep for `px` in CSS. Flag anything not on the 8pt grid.
- **Font declarations** — `font-family` strings. Flag anything that isn't DM Sans / DM Mono.
- **Ad-hoc components** — duplicated buttons, cards, inputs across files.

Produce a findings report.

## Step 2 — Tokenize

For every raw value that maps to a brand token, replace with the token:

- Colors → `var(--db-*)` from `brand-tokens.css`.
- Spacing → `var(--db-space-*)`.
- Fonts → `var(--db-font-primary)` / `var(--db-font-mono)`.
- Radius → `var(--db-radius-*)`.
- Shadows → `var(--db-shadow-*)`.
- Durations → `var(--db-duration-*)`.

For values that don't map (edge cases), add a new token to `brand-tokens.css` with a comment explaining why. Do not proliferate tokens — aim for reuse.

## Step 3 — Consolidate components

For each duplicated primitive:

- Check Du Bois for a shipped version. If it exists, replace the ad-hoc version with the Du Bois component.
- If Du Bois doesn't ship it, extract a single component matching Du Bois conventions (radius, density, state set).

Move extracted components to `src/components/design-system/` (or equivalent). Export from one barrel.

## Step 4 — Enforce

Add a lint rule or a pre-commit hook that forbids:

- Raw hex codes outside `brand-tokens.css`.
- `px` values in component CSS (tokens only).
- `font-family` strings outside `brand-tokens.css`.

For Tailwind projects: extend `tailwind.config.js` with the token set and disable the default palette.

## Step 5 — Document

Add a `design-system.md` to the repo showing:

- The tokens and when to use each.
- The available components (link to Storybook if one exists).
- Exceptions and their rationale.

## Output

Report what changed:

- Number of raw values replaced.
- Number of components consolidated.
- Any remaining exceptions that couldn't be tokenized (with reasons).
- Next steps to close the gap to full token coverage.
