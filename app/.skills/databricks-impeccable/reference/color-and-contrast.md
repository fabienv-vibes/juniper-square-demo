# Color & contrast — Databricks

The palette is fixed. This doc covers **how** to apply it, not which colors to pick.

## The authoritative palette

See `brand-tokens.json` for the complete set. Headliners:

```
Lava 600  #FF3621   — 10% accent only
Navy 900          #0B2026   — primary text, navbar
Light              #F9F7F4   — default background
Deep Navy          #003159   — alternate dark surface
Secondary Orange   #FF7033   — sparingly, charts/gradients
Yellow             #FBB300   — warning
Success            #00A972
Info               #4462C9
Error              #FF3621   (shares accent — use badge/icon for context)
```

## The 60-30-10 rule — Databricks edition

Visual *weight*, not pixel count:

- **60%** — Light `#F9F7F4` or Navy 900 `#0B2026` as dominant surface.
- **30%** — Secondary text and borders in neutrals.
- **10%** — Lava 600. That is the cap. Audit flags viewports where orange pixels exceed 10%.

Why: Lava 600 is precious. If everything is orange, nothing is.

## Theme selection

Theme is not a preference — it's derived from context.

- **Light (default)** — product UIs, internal dashboards, docs, customer-facing web apps, slides shown in meeting rooms.
- **Dark** — observability consoles, terminal-adjacent UIs, streaming workload dashboards, code-first tooling, live demos in dim rooms.
- **Both** — apps used across both contexts. Default to light, respect `prefers-color-scheme`, and ship a toggle.

Do not default everything to dark "to look cool." Dark is a specific choice for specific contexts.

## Dark mode is not inverted

When building dark mode, do NOT invert the light palette. Rebuild the surface hierarchy:

| Role | Light | Dark |
|---|---|---|
| Background | `#F9F7F4` | `#0B2026` |
| Surface (card, panel) | `#FFFFFF` | `#143D4A` |
| Elevated surface | `#FFFFFF` + shadow | `#1B5162` (lighter, no shadow — shadows don't read on dark) |
| Text primary | `#0B2026` | `#FFFFFF` |
| Text secondary | `#6B7280` | `#E5E7EB` |
| Border | `#C4CCD6` | `#4A5568` |
| Accent | `#FF3621` | `#FF3621` (optionally `#FF5A47` for contrast against `#0B2026`) |

On dark: reduce DM Sans weight slightly — light text reads heavier. Use weight 400 where you'd use 500 on light.

## Contrast & WCAG

Non-negotiable minimums:

- Body text: **4.5:1** (WCAG AA) / 7:1 (AAA)
- Large text (≥ 18 px or ≥ 14 px bold): **3:1** (AA) / 4.5:1 (AAA)
- UI components and icons: **3:1** (AA)

Pre-checked Databricks combinations:

| Foreground | Background | Ratio | Verdict |
|---|---|---|---|
| `#0B2026` | `#F9F7F4` | 11.5:1 | AAA ✓ |
| `#0B2026` | `#FFFFFF` | 12.8:1 | AAA ✓ |
| `#FF3621` | `#FFFFFF` | 3.9:1 | Large text only ✓ / body text ✗ |
| `#FF3621` | `#0B2026` | 3.3:1 | Large text only ✓ / UI icon ✓ |
| `#FFFFFF` | `#0B2026` | 12.8:1 | AAA ✓ |
| `#FFFFFF` | `#FF3621` | 3.9:1 | Large text only ✓ / body text ✗ |
| `#6B7280` | `#F9F7F4` | 4.6:1 | AA body ✓ |

**Critical**: Lava 600 text on white is **not** WCAG AA for body text. Use it for headings (≥ 18 px bold) or as background with white text (≥ 14 px bold) or for icons. Never for body paragraphs.

## Chart palettes

From `brand-tokens.json#color.chart`:

**Categorical** (8 distinct):
`#FF3621`, `#4462C9`, `#00A972`, `#FBB300`, `#FF7033`, `#003159`, `#A0AEC0`, `#122A45`

**Sequential** (Orange gradient, low → high):
`#F9F7F4` → `#FFDDD8` → `#FFB8AD` → `#FF9482` → `#FF7057` → `#FF4C2C` → `#FF3621` → `#CC2B1A`

**Diverging** (Navy ↔ Orange, negative ↔ positive):
`#003159` → `#1A5A7F` → `#4A8AB5` → `#A0C4D9` → `#FFD4CC` → `#FF9482` → `#FF5C3D` → `#FF3621`

Do not introduce chart colors outside these palettes without a documented reason.

## Rules

**DO**:

- Tint neutrals toward the brand. Our neutrals are already warm-tinted toward `#F9F7F4` — stay warm.
- Use `color-mix()` for alpha tints: `color-mix(in oklch, var(--db-accent) 12%, transparent)` for subtle accent backgrounds.
- Use `light-dark()` CSS function when targeting modern browsers.
- Check contrast for every foreground/background pair. No exceptions.

**DO NOT**:

- Use pure black `#000` or pure white `#FFFFFF` as large surfaces — they look sterile. Use `#0B2026` and `#F9F7F4` instead. (`#FFFFFF` is fine for elevated cards on `#F9F7F4`.)
- Use gray text on a colored background — use a shade of that color instead.
- Use the AI palette: cyan-on-dark, purple-to-blue gradients, neon accents.
- Use red/green as the only distinction in data viz — color-blindness accessibility failure. Pair with shape or icon.
- Use `#FF3621` as body-text color on white.
- Use Lava 600 for more than 10% of the viewport.
