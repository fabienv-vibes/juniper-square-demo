---
name: typeset
description: "Fix typography in a Databricks interface — hierarchy, sizing, line-height, weight, readability. DM Sans + DM Mono are locked; use this when sizes are muddled, hierarchy is flat, or body text is cramped."
argument-hint: "[target]"
user-invocable: true
---

## MANDATORY PREPARATION

Invoke `/databricks-impeccable:databricks-impeccable`. If no Design Context, run `/databricks-impeccable:databricks-impeccable teach` first.

---

The font choice is **not** in scope — DM Sans (400/500/700) and DM Mono (400/500) are locked. This skill fixes scale, hierarchy, readability, and consistency within those fonts.

## Assess

1. **Font load** — DM Sans actually loading, or falling through to system font? Check Network tab.
2. **Scale** — using `--db-text-*` tokens, or arbitrary sizes?
3. **Hierarchy contrast** — 1.25+ ratio between adjacent levels? Or muddled (14/15/16 px)?
4. **Line length** — body capped at 65-75ch?
5. **Line height** — tighter for headings (1.2-1.4), looser for body (1.5-1.7)?
6. **Weight discipline** — using only 400/500/700, or scattered across more?
7. **Numeric alignment** — `tabular-nums` on data tables?

## Improve

### Adopt the fixed scale (product UI)

```css
h1 { font-size: var(--db-text-h1); line-height: 1.2; font-weight: 700; letter-spacing: -0.02em; }
h2 { font-size: var(--db-text-h2); line-height: 1.3; font-weight: 700; letter-spacing: -0.01em; }
h3 { font-size: var(--db-text-h3); line-height: 1.4; font-weight: 700; }
h4 { font-size: var(--db-text-h4); line-height: 1.5; font-weight: 700; }
body { font-size: var(--db-text-body); line-height: 1.6; font-weight: 400; }
small { font-size: var(--db-text-small); line-height: 1.5; font-weight: 400; }
```

### Marketing pages use `clamp()` — product UI does not

Fluid type undermines dense layouts. Only use `clamp()` for h1/h2 on hero/marketing surfaces.

### Cap line length

```css
.prose { max-width: 65ch; }
.wide-prose { max-width: 75ch; }
```

### Numeric tables

```css
.data-table td.numeric {
  font-variant-numeric: tabular-nums;
  text-align: right;
}
```

### Code blocks

```css
code, pre { font-family: var(--db-font-mono); font-variant-ligatures: none; }
```

## Rules

**Do**:
- DM Sans + DM Mono only.
- Fixed `rem` scale for product UI.
- `font-display: swap` with metric-matched fallback.
- Sentence case for headings.
- `rem` not `px` for font sizes.

**Don't**:
- Substitute a different font.
- Use more than four weights (400/500/700 covers every role; occasional 500-italic for emphasis).
- Set body text below 16 px.
- Use `text-transform: uppercase` on long passages.
- Disable browser zoom.

## Output

Before/after type scale. Confirm DM Sans is rendering, not falling through to fallback.
