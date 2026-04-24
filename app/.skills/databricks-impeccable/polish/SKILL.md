---
name: polish
description: "Final pass on a Databricks interface — alignment, consistency, interaction state completeness, brand token enforcement, and micro-details. Use after building is done, before shipping."
argument-hint: "[area]"
user-invocable: true
---

## MANDATORY PREPARATION

Invoke `/databricks-impeccable:databricks-impeccable` — brand rules, AI-slop bans. If no Design Context, run `/databricks-impeccable:databricks-impeccable teach` first.

---

Final refinement pass. Fixes alignment, consistency, missing states, off-grid spacing, raw colors, and small off-brand moments. Do **not** redesign in this skill — polish only.

## Polish checklist

Work through in order. Each item = pass/fail; fix the failures.

### Token enforcement

- [ ] All colors reference `--db-*` (no raw hex in component files).
- [ ] All spacing uses `--db-space-*` (no raw `px` except in the token file).
- [ ] All fonts use `var(--db-font-primary)` / `var(--db-font-mono)`.
- [ ] All radii use `--db-radius-*`.
- [ ] All durations/easings use `--db-duration-*` / `--db-ease-*`.

### Alignment & rhythm

- [ ] Text and controls align to the 8pt grid.
- [ ] Optical adjustments applied where geometric centering looks off (icons in buttons, text next to icons).
- [ ] Repeated elements (cards, table rows) have identical spacing and height.
- [ ] Labels sit at consistent distance from their controls.
- [ ] Form fields have matching height and padding.

### Interaction states

Every interactive element has all eight:

- [ ] Default
- [ ] Hover (desktop)
- [ ] Focus (`:focus-visible`, 2 px accent ring)
- [ ] Active
- [ ] Disabled (with clear visual cue, not just reduced opacity)
- [ ] Loading (spinner or skeleton)
- [ ] Error (if applicable)
- [ ] Success (if applicable)

### Typography

- [ ] Heading scale consistent (no stray sizes).
- [ ] Line height appropriate per level.
- [ ] Body text ≥ 16 px.
- [ ] Line length capped at 65-75ch for long prose.
- [ ] DM Sans loaded with `font-display: swap` and a metric-matched fallback.
- [ ] `font-variant-numeric: tabular-nums` on numeric columns.

### Color & contrast

- [ ] All text meets WCAG AA (4.5:1 body, 3:1 large/UI).
- [ ] Lava 600 ≤ 10% of viewport.
- [ ] No gray text on colored backgrounds.
- [ ] Dark mode (if present) renders correctly, doesn't just invert.

### Voice pass

- [ ] Headings in sentence case.
- [ ] Button labels are imperative verbs.
- [ ] No banned marketing words (pivotal, leverage, seamless, unlock, transform, unleash, powerful, robust).
- [ ] Errors explain what happened and what to do.
- [ ] Empty states teach, don't just announce.

### Consistency

- [ ] Same role = same style everywhere (all primary buttons look identical).
- [ ] Same content type = same layout (all user cards align the same).
- [ ] Icons from one library only (Du Bois icons OR Lucide, not mixed).
- [ ] One primary button per screen.

### Micro-details

- [ ] Cursor types: `pointer` on interactive, `text` on text inputs, `not-allowed` on disabled.
- [ ] Smooth scrolling (`scroll-behavior: smooth`) unless it conflicts with animation.
- [ ] `:focus-visible` offsets the ring from the element (2 px gap).
- [ ] Transitions on hover states don't exceed 150 ms.

### Brand compliance (final hardline)

- [ ] DM Sans is the rendered font on every surface.
- [ ] Logo SVG imported, not recreated.
- [ ] Colors entirely within `brand-tokens.json`.
- [ ] Orange usage measured ≤ 10%.

## Output

After the pass, report:

- Items fixed (grouped by category).
- Items not fixed and why (e.g., requires redesign — run `/databricks-impeccable:layout` or `distill`).
- Final audit score estimate.

**Do not redesign in polish.** If an issue needs structural change, flag it for `shape`, `layout`, or `distill` instead.
