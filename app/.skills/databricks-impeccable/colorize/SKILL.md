---
name: colorize
description: "Strategically introduce Databricks brand color to a grayscale or monochromatic interface — without exceeding the 10% Lava 600 budget. Use when the UI looks flat, gray, or lacks hierarchy cues."
argument-hint: "[target]"
user-invocable: true
---

## MANDATORY PREPARATION

Invoke `/databricks-impeccable:databricks-impeccable`. If no Design Context, run `/databricks-impeccable:databricks-impeccable teach` first.

---

Add color *strategically*, not everywhere. The Databricks palette is small on purpose — every color placement should earn its spot.

## Assess

1. **Current state** — grayscale everywhere? One timid accent? Rainbow chaos?
2. **Opportunities** — where could color help hierarchy, meaning, or wayfinding?
3. **Orange budget** — measure current orange usage. Is there room (≤ 10% ceiling)?
4. **Context** — product UI (restrained) or marketing surface (richer)?

## Where color earns its spot

### Semantic states

- Success: `var(--db-success)` (#00A972) — confirmations, healthy status, completed jobs.
- Warning: `var(--db-warning)` (#FBB300) — caution, soft degradation.
- Error: `var(--db-error)` (#FF3621) — failure, blocking issues. (Shares color with accent — disambiguate with icon/badge context.)
- Info: `var(--db-info)` (#4462C9) — links, informational notes, "did you know".

### Brand moments (Lava 600 — the 10%)

- The single primary CTA per screen.
- Active nav item indicator.
- Focus ring.
- The Spark mark in the navbar.
- A single key metric's accent line on charts.

Do not scatter orange through secondary buttons, row dividers, or decoration. It loses power.

### Neutral warmth

Default backgrounds are warm (`#F9F7F4`), not cold gray. Where you have a cooler gray, warm it toward the brand:

```css
/* before */ background: #F5F5F5;
/* after  */ background: var(--db-light);
```

### Chart categories

Use the palette from `brand-tokens.json#color.chart`:

```js
const categoricalPalette = ['#FF3621', '#4462C9', '#00A972', '#FBB300', '#FF7033', '#003159', '#A0AEC0', '#122A45'];
```

For sequential (heatmap, density), use the orange gradient. For diverging (negative-to-positive), use the navy-to-orange palette.

## Rules

**Do**:
- Check the 10% orange budget before adding orange.
- Use semantic colors for status.
- Use chart palettes for data viz.
- Warm up cold grays.
- Use `color-mix()` for subtle tint backgrounds: `color-mix(in oklch, var(--db-accent) 12%, transparent)`.

**Don't**:
- Introduce colors outside `brand-tokens.json`.
- Use purple, magenta, or cyan.
- Use orange on orange.
- Use `#FF3621` as body text on white (fails WCAG AA).
- Add color to every element — hierarchy needs contrast.
- Use color as the only distinction in data viz (pair with shape/icon).

## Output

Before/after orange-usage percentage. List of color placements added and their purpose.
