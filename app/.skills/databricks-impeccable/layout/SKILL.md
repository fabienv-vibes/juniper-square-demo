---
name: layout
description: "Improve spacing, rhythm, and visual hierarchy of a Databricks interface. Fix monotone spacing, cramped or over-spaced areas, and weak hierarchy. Use when the layout feels flat, busy, or unfocused."
argument-hint: "[area]"
user-invocable: true
---

## MANDATORY PREPARATION

Invoke `/databricks-impeccable:databricks-impeccable`. If no Design Context, run `/databricks-impeccable:databricks-impeccable teach` first.

---

Improve the spatial rhythm of a surface.

## Assess

1. **Hierarchy squint test** — blur the UI. Does primary content dominate? Can you see groupings?
2. **Spacing variance** — is spacing varying with hierarchy, or is the same padding applied everywhere?
3. **Grid adherence** — are all gaps on the 8pt scale (4/8/16/24/32/48/64)?
4. **Line length** — body text wider than 75ch?
5. **Touch targets** — interactive elements < 44 × 44 px?
6. **Container queries** — components responding to container width, or only viewport?

## Improve

### Vary spacing by hierarchy

- `--db-space-xs` (4 px) — internal gaps in tight controls.
- `--db-space-sm` (8 px) — icon-to-text, tight form rows.
- `--db-space-md` (16 px) — default body spacing, card padding.
- `--db-space-lg` (24 px) — section padding, card-to-card.
- `--db-space-xl` (32 px) — major section breaks.
- `--db-space-2xl` (48 px) — page-level sections.
- `--db-space-3xl` (64 px) — hero to content, page to footer.

Headings get more space above than the surrounding body:
```css
h2 { margin-top: var(--db-space-2xl); }
h3 { margin-top: var(--db-space-xl); }
```

### Self-adjusting grids

For repeating cards or tiles:
```css
display: grid;
grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
gap: var(--db-space-lg);
```

### Container queries for components

A card in a sidebar adapts to the sidebar width, not the viewport.

### Break symmetry where it helps

- Asymmetric splits (60/40, 2fr/1fr) read as more designed than everything 50/50.
- Left-align content blocks; avoid centering everything.
- One or two elements slightly off-grid can add intentional rhythm — but not on the 8pt fundamentals.

## Rules

**Do**:
- Use `gap`, not margins.
- Use semantic tokens.
- Mobile-first breakpoints at sm/md/lg/xl.
- 44 × 44 px touch targets minimum.
- Cap body at 65-75ch.

**Don't**:
- Use the same padding everywhere.
- Wrap everything in cards.
- Nest cards in cards.
- Center-align by default.
- Let body text wrap past 80 characters.

## Output

Before/after summary of spacing changes. Call out which hierarchy levels got clearer.
