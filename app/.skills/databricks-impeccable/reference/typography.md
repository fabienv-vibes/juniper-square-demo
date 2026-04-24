# Typography — Databricks

The typography choice is **pre-made**: DM Sans for text, DM Mono for code. This override is deliberate — upstream impeccable forbids DM Sans as a "reflex font" because it became a 2024 monoculture default. For Databricks, it is the brand. Use it.

## Fonts

### DM Sans (primary)

- **Weights used**: 400 (Regular), 500 (Medium), 700 (Bold). Do not load weights you won't use.
- **Italics**: available in 400. Use sparingly — for citations, emphasis within running body text, legal copy.
- **CDN**: `https://fonts.googleapis.com/css2?family=DM+Sans:ital,wght@0,400;0,500;0,700;1,400&display=swap`
- **Loading**: `font-display: swap` always. Metric-matched fallback is `ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif` — the x-height and cap-height are close enough to avoid heavy FOUT.

### DM Mono (code)

- **Weights used**: 400, 500.
- **CDN**: `https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&display=swap`
- **Use for**: code blocks, terminal output, API endpoints inline, numeric tables that need `tabular-nums`.

### Fallbacks

Do not ship apps without a metric-matched fallback. If the CDN fails, text must still render at the correct size:

```css
font-family: 'DM Sans', ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
```

For enterprise deployments behind proxies, self-host the WOFF2 files from `https://fonts.bunny.net/dm-sans/` as a fallback CDN.

## Type scale

### Web / app UI (fixed `rem`)

Use the fixed `rem` scale for product surfaces. Fluid `clamp()` undermines dense, container-based layouts.

| Level | Size | Line-height | Weight | Letter-spacing |
|---|---|---|---|---|
| h1 | 3 rem (48 px) | 1.2 | 700 | -0.02em |
| h2 | 2.25 rem (36 px) | 1.3 | 700 | -0.01em |
| h3 | 1.5 rem (24 px) | 1.4 | 700 | 0 |
| h4 | 1.125 rem (18 px) | 1.5 | 700 | 0 |
| body | 1 rem (16 px) | 1.6 | 400 | 0 |
| small | 0.875 rem (14 px) | 1.5 | 400 | 0 |
| code | 0.875 rem (14 px) | 1.6 | 400 (DM Mono) | 0 |

### Marketing / content pages (fluid)

Use `clamp()` for h1/h2 on hero and marketing surfaces only:

```css
h1 { font-size: clamp(2.5rem, 5vw + 1rem, 4.5rem); }
h2 { font-size: clamp(1.75rem, 3vw + 1rem, 3rem); }
```

Body stays fixed at 1 rem.

### Slides / PDFs (pt)

See `brand-tokens.json#typography.scale_slides_pt` — matches the official Databricks slide theme. h1 is 48 pt title-slide, 36 pt normal slide.

## Hierarchy rules

- Use a 1.5 ratio between adjacent levels (h3 → h2 → h1) for clear contrast.
- Cap at **4 levels** (h1–h4) + body + small. Avoid h5/h6 — if you need them, restructure content.
- Combine dimensions: size + weight + color. Never rely on size alone.
- Headings: `#0B2026` (Navy 900). Body: `#0B2026` at 80-100% opacity depending on context. Secondary text: `#6B7280`.

## Readability

- Body text: **at least 16 px**. Never below.
- Line length: cap at **65-75ch** (`max-width: 65ch`). Body wider than that fatigues the eye.
- Line-height scales inversely with line length. Wide columns: 1.6-1.7. Narrow columns: 1.4-1.5. Headings: 1.2-1.4.
- For light text on dark backgrounds, add 0.05 to line-height — light text reads as lighter weight and needs more breathing room.
- `font-kerning: normal` always.

## OpenType features

DM Sans ships with useful OpenType features — enable them:

```css
:root {
  font-feature-settings: "kern", "liga", "calt";
}

.tabular {
  font-variant-numeric: tabular-nums;
}

.fraction {
  font-variant-numeric: diagonal-fractions;
}

.small-caps {
  font-variant-caps: all-small-caps;
  letter-spacing: 0.04em;
}
```

In code blocks, disable ligatures so characters don't merge unexpectedly:

```css
code, pre { font-variant-ligatures: none; }
```

## Token naming

Use **semantic** tokens, not value-based. The type scale may adjust; call sites should not.

```css
/* YES */
--db-text-body: 1rem;
--db-text-heading: 2.25rem;

/* NO */
--font-16: 1rem;
--font-36: 2.25rem;
```

## Rules

**DO**:

- Use DM Sans. Always.
- Use DM Mono for code, numeric tables, terminal output.
- Use `rem`, not `px`, for font sizes — respect user OS settings.
- Use `ch` units for text container `max-width`.
- Set `font-display: swap`.

**DO NOT**:

- Substitute DM Sans with Inter, Roboto, Open Sans, IBM Plex, Circular, or any other font.
- Use decorative or display fonts anywhere. Databricks has one voice.
- Set body text below 16 px.
- Use uppercase for long passages — reserve `text-transform: uppercase` for short labels and small-caps UI.
- Disable browser zoom (`user-scalable=no`). Accessibility violation.
- Use 5+ font weights — 400/500/700 cover every role.
- Let lines wrap past 80 characters without a `max-width`.
