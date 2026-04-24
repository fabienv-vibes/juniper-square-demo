# Responsive — Databricks

Build **adaptively**, not restrictively. Do not hide critical functionality on mobile — the same feature should exist in a context-appropriate form.

## Approach

- **Mobile-first** — start with the narrowest viable viewport, add capability upward.
- **Container queries for components**. Viewport queries for page shell.
- **Fluid spacing** with `clamp()` for marketing pages; fixed `--db-space-*` for product UIs.

## Breakpoints

Databricks product UIs target four breakpoints:

| Name | Min width | Use for |
|---|---|---|
| sm | 0 | Mobile — phones, compact tablets |
| md | 768 px | Tablets, narrow laptops |
| lg | 1024 px | Standard laptops, desktops |
| xl | 1440 px | Wide desktops, presentation screens |

Avoid custom breakpoints like 911 px or 1150 px. Pick one of the four.

## Container queries

Components adapt to their container, not viewport:

```css
.card-host { container-type: inline-size; }

.card { display: grid; gap: var(--db-space-md); }

@container (min-width: 400px) {
  .card { grid-template-columns: 120px 1fr; }
}
```

This means a card in a sidebar can stay vertical while the same card in a main panel goes horizontal — without viewport breakpoints.

## Touch

- **44 × 44 px minimum** for any interactive element.
- **Do not rely on hover**. Every hover affordance needs a touch equivalent (tap to reveal, long-press, explicit button).
- On touch devices, show native scrollbars or visible scroll cues — hidden scroll is disorienting.

## Text scaling

Users can zoom browser text to 200%. Layouts must survive this:

- Use `rem` for font sizes (not `px`).
- Avoid fixed-height containers that clip text when scaled.
- Test at 200% zoom on all breakpoints.

## Adaptive not restrictive

Hide-on-mobile is almost always wrong. If a data table is too wide:

- Wrong: hide columns.
- Right: horizontal scroll for power users, or collapse rows into expandable cards.

If a nav is too long:

- Wrong: remove items.
- Right: collapse to a menu with the same items.

## Images

- `<img srcset>` with 2-3 breakpoint variants.
- `loading="lazy"` for below-fold images.
- `<picture>` with WebP/AVIF sources + JPEG/PNG fallback.
- Always specify `width` and `height` or `aspect-ratio` to prevent CLS.

## Rules

**DO**:
- Mobile-first CSS cascade.
- Container queries for component responsiveness.
- `clamp()` for fluid marketing-page type only.
- 44 × 44 px touch targets.
- `rem` for text sizing.

**DO NOT**:
- Hide critical features on mobile.
- Use `display: none` at breakpoints as a shortcut — adapt instead.
- Use device detection (`navigator.userAgent`) — use feature queries.
- Disable zoom (`user-scalable=no`).
