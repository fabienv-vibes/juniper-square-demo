# Craft flow

Shape-then-build for a new UI surface. Takes the feature description from arguments, runs discovery, plans, builds, and verifies.

## Prerequisites

Design Context must exist (in CLAUDE.md, `.databricks-impeccable.md`, or `.impeccable.md`). If not, run `/databricks-impeccable:databricks-impeccable teach` first.

## Step 1 — Shape

Run the shape skill flow (see `skills/shape/SKILL.md`) to produce a design brief with:

- User journey (1-3 screens)
- Component inventory (Du Bois primitives needed + any custom)
- Content hierarchy
- Surface layout sketch (ASCII or described)

Do **not** write code yet.

## Step 2 — Scaffold

Based on the surface type in Design Context:

- **Databricks App (FastAPI+React)** — use the `databricks-apps` skill scaffold. Import `brand-tokens.css` in `src/index.css`. Default to Du Bois `@databricks/design-system`.
- **Streamlit** — inject `brand-tokens.css` via `st.markdown("<style>...</style>", unsafe_allow_html=True)`. Use `st.set_page_config(page_title=..., layout="wide")`. Streamlit theming via `.streamlit/config.toml` with `primaryColor = "#FF3621"`, `backgroundColor = "#F9F7F4"`, `textColor = "#0B2026"`, `font = "sans serif"`.
- **Dash** — include `brand-tokens.css` in `assets/`. Use Dash Mantine Components configured with the Databricks palette.
- **AI/BI dashboard** — use the categorical/sequential/diverging palettes from `brand-tokens.json#color.chart`.
- **PDF report** — use the `markdown-to-pdf` skill with `databricks_report.css` (already uses DM Sans + palette).
- **Slide deck** — use `databricks-slides` skill.

## Step 3 — Build

Implement the shape iteratively:

1. Base layout and navigation shell.
2. Primary content surface (dashboard, form, table).
3. Interactive states (hover, focus, active, disabled, loading, error, success).
4. Responsive behavior (container queries, touch targets).
5. Motion where it conveys meaning.

Each step uses only tokens — no raw colors, no raw pixel values for spacing, no inline fonts.

## Step 4 — Polish

Run `/databricks-impeccable:polish` — it handles alignment, consistency, interaction state completeness, and brand verification.

## Step 5 — Audit

Run `/databricks-impeccable:audit` — gets a P0–P3 scored report on accessibility, performance, theming, responsive, anti-patterns, and Databricks brand compliance.

## Step 6 — Iterate

Fix P0 and P1 findings. Re-run `/databricks-impeccable:audit`. Target: 18-20/20 before shipping.

## Verification

Before declaring complete:

- DM Sans loads (check Network tab).
- Lava 600 usage stays ≤ 10% of viewport.
- All interactive elements have focus-visible rings.
- Body text ≥ 16 px everywhere.
- Dark mode renders correctly (if applicable).
- Responsive down to 375 px without horizontal scroll (if applicable).
- No gradient text. No border-left/right > 1 px accents. No glassmorphism default.
