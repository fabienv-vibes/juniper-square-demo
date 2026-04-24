---
name: brand-assets
description: "Use when a UI, app, slide, PDF, or doc will reference a Databricks logo, Spark mark, product icon, concept icon, illustration, font, or any other Databricks brand visual. Activates before emitting any markup that includes <img>, inline <svg>, a logo component, a font file, or an asset path. Pulls the real asset from the shared Drive folder on demand."
argument-hint: "[asset name or 'find <description>']"
user-invocable: true
---

## MANDATORY PREPARATION

Invoke `/databricks-impeccable:databricks-impeccable` for brand rules first. Do not skip.

---

All official Databricks brand assets — 1,333 files covering wordmarks, Spark marks, product lockups, concept icons, illustrations, and DM Sans fonts — live in a shared Google Drive folder, catalogued in `assets/catalog.json` and `assets/CATALOG.md` in this plugin.

**Drive folder**: https://drive.google.com/drive/folders/1j2ZepZNHp9RXPJ8_Es4N6EVPt9ACo5Q8
**Sharing**: anyone at `@databricks.com` with the link can view.

## Core rule

**Never emit `<DatabricksLogo />`, inline SVG redraws of the Spark mark, CSS-art wordmarks, or a path like `/logos/databricks.svg` without first pulling the real asset from the catalog into the project.**

If the asset isn't on disk in the project you're building, stop, pull it, then emit the markup that references the real file.

## Flow

1. **Find the right filename.** Three lookup paths, in order:

   a. **Read `assets/CATALOG.md`** in this plugin — grouped by primary-brand / products / concept-icons / illustrations / fonts. Quick-pick table at the top covers the 80% cases.

   b. **Grep `assets/catalog.json`** for a concept. Example:
   ```bash
   jq '.index | to_entries[] | select(.key | contains("analytics"))' assets/catalog.json
   ```

   c. **Ask the user** if nothing fits — do not guess or invent a filename.

2. **Pull the asset into the project you're building.** Two methods — use whichever works in your environment:

   a. **Drive MCP (preferred inside Claude Code)** — use `mcp__claude_ai_Google_Drive__download_file_content` with the file ID from the catalog, then write the bytes to the project's asset directory (e.g. `public/logos/primary-lockup-full-color-rgb.svg`).

   b. **Fetch script (always works, needs gcloud auth)**:
   ```bash
   # from the databricks-impeccable plugin dir:
   ./scripts/fetch-asset.sh primary-lockup-full-color-rgb.svg /path/to/your/app/public/logos/databricks.svg
   ```
   First time only: `gcloud auth application-default login` with a `@databricks.com` account.

3. **Reference the real file** in your markup:
   ```html
   <img src="/logos/databricks.svg" alt="Databricks" width="120" height="24">
   ```
   Always `alt="Databricks"` — not "Databricks Inc.", not "the Databricks logo".

4. **Report what was fetched** — in your response, list: asset filename, where it was written, and how it's referenced.

## Quick reference — most-requested files

| You want… | Filename |
|---|---|
| Wordmark on light background | `primary-lockup-full-color-rgb.svg` |
| Wordmark on dark background | `primary-lockup-full-color-white-rgb.svg` |
| Wordmark mono (print) | `primary-lockup-one-color-navy-900-rgb.svg` |
| Stacked logo (marketing) | `stacked-lockup-full-color-rgb.svg` |
| Favicon | `stacked-lockup-full-color-rgb.svg` (export to .ico in your build) |
| Agent Bricks product icon | `agent-bricks-icon-full-color.svg` |
| Agent Bricks product lockup | `agent-bricks-lockup-full-color.svg` |
| AI/BI product icon | `ai-bi-icon-full-color.svg` |
| Lakeflow product icon | `lakeflow-icon-full-color.svg` |
| Unity Catalog OSS logo | `logo-color-unity-catalog-oss.svg` |
| MLflow logo | `mlflow-logo-black.svg` |
| Concept: analytics | `primary-icon-navy-analytics.svg` |
| Concept: ai | `primary-icon-navy-ai.svg` |
| Concept: automation | `primary-icon-navy-automation.svg` |
| Hero illustration (abstract) | browse `illustration-abstract-*.svg` in CATALOG.md |
| DM Sans Regular | `dm-sans-regular.ttf` |
| DM Sans Medium | `dm-sans-medium.ttf` |
| DM Sans Bold | `dm-sans-bold.ttf` |
| OFL license (ships with fonts) | `ofl.txt` |

## Variant axes

Most assets come in multiple variants. Read the filename:

- **color**: `full-color` (orange+navy) · `one-color` (single-tone) · `navy` / `orange` / `white` (for concept icons)
- **background**: `-white` suffix = for dark backgrounds · otherwise = for light/neutral
- **style**: `stacked` (vertical), `container` (with rounded bg), `no-db` (product mark without "Databricks" prefix), `-alt` (alternate composition), `-product` (with product-tier treatment)
- **format**: `.svg` for web · `.png` (4000px masters) for high-res raster · `.eps` for print · `.ttf` for fonts

Filenames encode all of these, e.g. `databricks-sql-lockup-full-color-white-container-stacked.svg` = full-color stacked container lockup for dark backgrounds.

## Rules

**Do:**
- Pull the real file from the Drive catalog before emitting markup that references it.
- Use the exact filename from `catalog.json` — don't rename on fetch unless normalising for your app's routing.
- Preserve the `#FF3621` fill on the Spark mark always.
- Include `alt="Databricks"` (or an appropriate product alt for product icons).
- Write the fetched asset into your project's public/static asset directory, not into this plugin's `assets/`.

**Don't:**
- Redraw any logo in inline SVG, CSS art, Unicode, or emoji.
- Use the logo as a typographic glyph inside a sentence.
- Recolor the Spark mark — Lava 600 (`#FF3621`) only.
- Rotate, skew, stretch, outline, or drop-shadow any logo.
- Reference a path like `/assets/logos/foo.svg` without confirming the file is on disk.
- Commit the SVGs to the databricks-impeccable repo — they stay gitignored; each user hydrates their own project on demand.

## Red flags — STOP

If you catch yourself thinking any of these, you're about to violate the core rule:

- "I'll just inline a simple SVG triangle as a placeholder."
- "The user can drop the file in later, I'll scaffold the markup now."
- "I'll use a Unicode character or emoji as a logo stand-in."
- "A generic `<Logo />` component is fine, they'll wire it up later."
- "I'll make up a plausible filename — the catalog probably has something like it."

**All of these mean: stop, read `CATALOG.md`, fetch the asset, then emit markup.**

## Output

When this skill runs, report:

- Asset(s) identified from the catalog (filename + Drive file ID).
- Where each was written in the target project (absolute or project-relative path).
- How each is referenced in the generated markup.
- Any requested asset that was not found in the catalog (explicitly flag — don't invent).
