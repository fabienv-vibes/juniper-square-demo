# Components — Du Bois first

Databricks has an internal React component library: **Du Bois**. Before building any custom primitive, check whether Du Bois already ships it.

## Links

- Storybook: https://ui-infra.dev.databricks.com/storybook/js/packages/du-bois/
- Package: `@databricks/design-system` (internal)
- Slack: `#dubois`

## Coverage at a glance

Du Bois ships most of what a product UI needs:

- **Form**: `Input`, `Textarea`, `Select`, `Combobox`, `Checkbox`, `Radio`, `Switch`, `DatePicker`, `Form`
- **Action**: `Button` (primary/secondary/tertiary), `SplitButton`, `DropdownMenu`, `IconButton`
- **Data**: `Table`, `Tree`, `Pagination`, `Tabs`
- **Feedback**: `Alert`, `Banner`, `Toast`, `Tooltip`, `Popover`, `Modal`, `Drawer`
- **Layout**: `Card`, `Collapse`, `Divider`, `Spacer`, `Typography`
- **Navigation**: `Breadcrumb`, `Menu`, `Pagination`, `Tabs`

If it's in Du Bois, use Du Bois — do not rebuild.

## When to build custom

Only when:

1. Du Bois doesn't ship the primitive.
2. The primitive exists but is unfit for the use case (rare — file a Du Bois issue first).
3. You're building a public demo where the Du Bois package can't be bundled (license-restricted contexts).

When building custom, **match Du Bois patterns**:

- Border radius: `--db-radius-md` (8 px) for inputs/buttons, `--db-radius-card` (12 px) for cards.
- Focus ring: 2 px solid `--db-accent`, 2 px offset.
- Density: medium. Don't build compact unless the interface is heavily data-dense.
- Motion: see `motion-design.md`.

## Typography components

Du Bois `Typography` has: `Title`, `Text`, `Paragraph`, `Link`. Use these instead of raw `<h1>` / `<p>` in Du Bois-heavy apps — it guarantees the type scale matches.

For non-Du-Bois apps, use raw HTML with `brand-tokens.css` token-driven sizing.

## Icons

Databricks uses **Lucide** as the default icon library for non-Du-Bois apps (`lucide-react` on npm). Du Bois ships its own icon set (`@databricks/design-system/dist/icons`) — in Du Bois apps use those.

- Default size: 16 px for inline, 20 px for buttons, 24 px for navigation.
- Stroke width: 1.5-2 px. Never thicker than 2.
- Color: inherit from text color (`currentColor`). Don't recolor icons to `--db-accent` unless the icon represents an accent moment (CTA, active state, critical alert).

## Example — Du Bois button

```jsx
import { Button } from '@databricks/design-system';

<Button type="primary" onClick={handleRun}>
  Run job
</Button>
```

## Example — custom button matching Du Bois

```jsx
// When Du Bois is unavailable (external demo, embedded widget)
<button
  className="db-btn db-btn-primary"
  onClick={handleRun}
>
  Run job
</button>
```

```css
.db-btn {
  font-family: var(--db-font-primary);
  font-size: var(--db-text-body);
  font-weight: 500;
  padding: var(--db-space-sm) var(--db-space-lg);
  border-radius: var(--db-radius-md);
  border: 1px solid transparent;
  cursor: pointer;
  transition: background var(--db-duration-instant) var(--db-ease-quart);
}

.db-btn-primary {
  background: var(--db-accent);
  color: #FFFFFF;
}

.db-btn-primary:hover {
  background: color-mix(in oklch, var(--db-accent) 90%, #000);
}

.db-btn-primary:focus-visible {
  outline: 2px solid var(--db-accent);
  outline-offset: 2px;
}
```

## Checking before building

For every component you're about to write:

1. Search Du Bois Storybook.
2. If present, use it. If not, check `#dubois` on Slack — it may be in-flight.
3. Only if neither — build custom, matching Du Bois conventions.

This check takes 30 seconds and saves days of inconsistency debt.
