# Spatial design — Databricks

Databricks uses an **8pt grid** (overrides impeccable's 4pt default) for consistency with the official slide theme and Du Bois components.

## The scale

```
xs   4px    hairline dividers, icon-to-text in tight contexts
sm   8px    tight spacing, internal padding of small controls
md   16px   default body spacing, paragraph gaps, card padding
lg   24px   section padding, card-to-card, generous list gaps
xl   32px   major section breaks within a page
2xl  48px   page-level section separation
3xl  64px   hero → content, page → footer
```

Use `--db-space-*` tokens, never raw pixel values in component code.

## Rules

### Use `gap`, not margins

```css
/* YES */
.stack { display: flex; flex-direction: column; gap: var(--db-space-md); }

/* NO */
.child + .child { margin-top: 16px; }
```

`gap` eliminates margin collapse and the cleanup hacks that come with it.

### Vary spacing for hierarchy

A heading with extra space above it reads as more important. Don't apply the same padding everywhere:

```css
h2 { margin-top: var(--db-space-2xl); }
h3 { margin-top: var(--db-space-xl); }
h4 { margin-top: var(--db-space-lg); }
p + p { margin-top: var(--db-space-md); }
```

### Self-adjusting grids

Use breakpoint-free responsive grids for card-style content:

```css
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--db-space-lg);
}
```

### Container queries over viewport queries

Components should adapt to their container, not the viewport. A card in a sidebar responds to the sidebar width, not the window.

```css
.card-container { container-type: inline-size; }

@container (min-width: 400px) {
  .card { grid-template-columns: 120px 1fr; }
}
```

Viewport queries are for **page layout** (shell, navigation placement). Container queries are for **components**.

## Visual rhythm

Squint at your UI. Can you identify primary / secondary / groupings when blurred?

- Size ratio of **3:1+** between primary and supporting content reads clearly.
- Use size, weight, color, position, and space — multiple dimensions, not size alone.
- Left-aligned text with asymmetric layouts feels more designed than centered everything.

## Cards — use with restraint

Upstream impeccable says "cards are not required." For Databricks that's softened: Du Bois ships a `Card` primitive, so cards are allowed and encouraged for product surfaces — but not everything needs one.

**Use a card when**:
- Content represents a discrete entity (one job, one workspace, one dashboard tile)
- You need an elevation break from the page background
- The surface is interactive as a whole (click-through)

**Don't use a card when**:
- It's just a paragraph of body copy
- You're nesting cards inside cards — flatten
- You have an identical 4-up grid of cards with icon + heading + two-line-description (AI template tell)

Card defaults: `border-radius: var(--db-radius-card)` = 12 px. Padding `var(--db-space-lg)` = 24 px. Background `#FFFFFF` on light, `#143D4A` on dark.

## Touch targets

Minimum **44 × 44 px** for any interactive element. This includes navbar icons, table row buttons, close-X on modals.

If the visible element is smaller (like a 16 px close icon), expand the hit area with padding or a pseudo-element, not just visual size.

## Depth & elevation

Semantic z-index scale (define once, use everywhere):

```css
--z-dropdown:       1000;
--z-sticky:         1100;
--z-modal-backdrop: 1200;
--z-modal:          1210;
--z-toast:          1300;
--z-tooltip:        1400;
```

Shadows from `brand-tokens.css` (`--db-shadow-sm/md/lg/xl`). On dark backgrounds shadows barely read — use lighter surfaces for depth instead.

## Rules

**DO**:
- 8pt grid, semantic tokens.
- `gap` for sibling spacing.
- Vary spacing by hierarchy, not uniform.
- Container queries for components.
- Left-aligned text with asymmetric layouts.

**DO NOT**:
- Wrap everything in cards — whitespace works.
- Nest cards in cards.
- Use identical card grids repeated 6+ times.
- Center everything.
- Let body text wrap beyond 80 characters (`max-width: 65ch`).
- Mix the grid with arbitrary pixel values (21 px, 13 px).
- Use margin-top and margin-bottom on the same element.
