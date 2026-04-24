# Motion — Databricks

Databricks motion is **confident, not flashy**. Shorter durations, restrained easing, purposeful. No bounces, no elastic, no decorative overdrive.

## Duration

Use the `--db-duration-*` tokens. Never inline arbitrary milliseconds.

| Token | ms | Use for |
|---|---|---|
| `--db-duration-instant` | 100 | Hover state, button press, focus ring |
| `--db-duration-fast` | 150 | Tooltip fade, checkbox toggle, switch |
| `--db-duration-state` | 250 | Dropdown open/close, accordion, tab change |
| `--db-duration-layout` | 400 | Drawer slide-in, modal entrance |
| `--db-duration-entrance` | 600 | Page-load staggered reveals (marketing only) |

Exit durations are ~75% of enter durations — disappearing motion is faster than arriving motion.

## Easing

Only exponential curves. Linear looks mechanical, bounces look tacky.

| Token | Curve | Use for |
|---|---|---|
| `--db-ease-quart` | `cubic-bezier(0.25, 1, 0.5, 1)` | Default — dropdowns, tabs, most state changes |
| `--db-ease-quint` | `cubic-bezier(0.22, 1, 0.36, 1)` | Slightly more dramatic — drawers, large panels |
| `--db-ease-expo` | `cubic-bezier(0.16, 1, 0.3, 1)` | Snappy, used sparingly — entrance of the hero element |

No `ease-out-bounce`. No `ease-out-elastic`. No `spring()` physics beyond what's needed for dragging.

## What to animate

**Only `transform` and `opacity`.** Everything else triggers layout recalculation.

For height animations (accordion, reveal), use CSS grid:

```css
.panel {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows var(--db-duration-state) var(--db-ease-quart);
}
.panel.open { grid-template-rows: 1fr; }
.panel > div { overflow: hidden; }
```

Never animate `height`, `width`, `padding`, `margin`, `top`, `left`.

## Staggering

For entrance reveals of multiple items, stagger — but cap total stagger time so the last item doesn't feel late:

```css
.item { animation-delay: calc(var(--i, 0) * 50ms); }
```

For 8 items × 50 ms = 400 ms total. If you have 20 items, reduce the step to 20-30 ms.

## Reduced motion

**Required.** ~35% of adults over 40 prefer reduced motion. Already set in `brand-tokens.css`:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

Don't work around it. If your motion is essential to meaning, provide a non-motion fallback that communicates the same thing (checkmark icon instead of animated tick).

## Rules

**DO**:
- Use the duration + easing tokens.
- Animate `transform` and `opacity` only.
- Grid-template-rows trick for height transitions.
- Stagger entrance reveals with a capped total time.
- Respect `prefers-reduced-motion`.

**DO NOT**:
- Animate layout properties.
- Use bounce or elastic easing.
- Add motion that has no function. Decorative motion is the first thing that feels AI-generated.
- Autoplay animations on page load more than once — a single staggered entrance is plenty.
- Use parallax scroll without a strong reason. It disorients on trackpads and breaks scroll performance.
