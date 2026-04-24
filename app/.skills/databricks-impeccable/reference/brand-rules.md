# Databricks brand rules

This is the rulebook. Every `/databricks-impeccable:*` skill defers to it. Violations are P0/P1 findings in `audit`.

## The palette is fixed

| Token | Hex | Role |
|---|---|---|
| Lava 600 | `#FF3621` | **10% accent only** — CTAs, highlights, brand moments |
| Navy 900 | `#0B2026` | Primary text, navbar, dark surfaces |
| Light (warm off-white) | `#F9F7F4` | Default background |
| Deep Navy | `#003159` | Alternate dark surface |
| Secondary Orange | `#FF7033` | Use sparingly — charts, gradients |
| Yellow | `#FBB300` | Warning accent |
| Success | `#00A972` | Confirmations, positive state |
| Info | `#4462C9` | Informational accents, links |
| Error | `#FF3621` | Matches accent — use badge/icon context to disambiguate |

**Do not introduce brand colors outside this list.** Need a new shade? Derive from the extended palette in `brand-tokens.json` (`--db-teal`, `--db-rose`, `--db-dark-mid`) — don't invent.

## The 60-30-10 rule (Databricks edition)

- **60 %** — Light (`#F9F7F4`) or Navy 900 (`#0B2026`) as the dominant surface.
- **30 %** — Secondary text and borders in neutrals (`--db-neutral-700`, `--db-border`).
- **10 %** — Lava 600 (`#FF3621`). That's the cap. It includes CTAs, the top-bar accent line, link underlines, status dots, anything orange.

If your viewport screenshot is more than 10% orange pixels, you are overusing the accent. `audit` will flag this.

## Typography

- **Primary**: DM Sans 400 / 500 / 700. Loaded via Google Fonts CDN. Never substitute.
- **Monospace**: DM Mono 400 / 500 for code and `tabular-nums` contexts.
- **Fallback stack** (for FOUT/FOIT prevention and offline builds): `ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif`.
- Do **not** use Inter, Roboto, Open Sans, IBM Plex, Circular, system defaults without fallback, or any decorative/display face. Databricks has exactly one voice: DM Sans.
- See `typography.md` for the full scale.

## Logo use

- **Wordmark** — use `assets/logos/databricks-wordmark-dark.svg` on light backgrounds, `databricks-wordmark-light.svg` on dark.
- **Spark mark** (square, orange) — `assets/logos/spark-mark.svg`. Use for favicons, navbar icons, app icons. Never strip the orange fill.
- **Clear space** — minimum `2x` the height of the lowercase "d" on all sides. Never crowd.
- **Minimum width** — wordmark 120 px on screen, 24 mm in print. Spark mark 24 px on screen.
- **Do NOT**: rotate, skew, recolor, outline, drop shadow, emboss, add gradient fill, place on busy photography without a tinted scrim, stretch disproportionately, or embed in other shapes.
- **Never**: use the logo as body-text character ("Databricks is the 🟠 company") or as decorative filler.

## Voice

Adopt the Databricks editorial voice for all UI copy and docs output:

- No superlatives: avoid "pivotal", "critical", "key", "significant", "strong", "powerful", "unlock", "transform", "seamless".
- No slide-deck framing: avoid "strategic frame", "expansion path", "core narrative", "force multiplier", "beachhead".
- Headers are labels, not taglines.
- Say "unknown" or "not yet confirmed" instead of hedging with soft language.
- Plain statements over marketing. "Process runs every 15 minutes" beats "Effortlessly harness real-time insights every quarter-hour."
- Active voice. Second person ("you") for product UI; third person for reference docs.

## The AI Slop bans (inherited from impeccable)

Preserved verbatim — these are pattern matchers, not judgment calls:

- **BAN — side-stripe borders**: `border-left` or `border-right` >1px on cards, list items, alerts, callouts. Hard-coded color or CSS variable. This is the single most overused AI design tell.
- **BAN — gradient text**: `background-clip: text` combined with any gradient. Solid colors only for text.
- **BAN — purple-to-blue gradients**: the AI aesthetic. If you want a gradient, use Orange → Deep Navy along the diagonal.
- **BAN — glassmorphism by default**: blurred glass cards are never a default. Only use if the product has a specific reason.
- **BAN — hero metric template**: big number, small label, supporting stats, gradient accent — every SaaS landing page.
- **BAN — bounce/elastic easing**: use exponential easing (see `motion-design.md`).

## Databricks-specific bans

- **Orange on orange** — Lava 600 text on a Lava 600 surface. Illegible and off-brand.
- **Navy on navy** — same issue inverted (Navy 900 text on Navy 900 surface). Audit will flag contrast failures.
- **Blue as primary** — Databricks is not a blue-accent company. Info blue (`#4462C9`) exists for informational moments only; it is never the dominant accent.
- **Purple / Magenta** — outside the palette. Do not introduce.
- **Emoji as visual primitive** — use Du Bois icons or Lucide. Emoji only when the user explicitly asks.
- **Logo reconstruction** — do not redraw or SVG-approximate the wordmark. Import the file.

## Du Bois first

Before writing a custom component, check whether Du Bois already ships it.

- Storybook: https://ui-infra.dev.databricks.com/storybook/js/packages/du-bois/
- Slack: `#dubois`
- If Du Bois ships it, use Du Bois. If Du Bois doesn't, build a custom component that *matches* Du Bois patterns (radius, density, focus ring, state set).

## When in doubt

1. Read `brand-tokens.json` — use the tokens, don't invent.
2. Run `/databricks-impeccable:audit` before committing.
3. Ask in `#dubois` if the component pattern is unclear.
4. Never apologize for sticking to the palette. Brand consistency is the point.
