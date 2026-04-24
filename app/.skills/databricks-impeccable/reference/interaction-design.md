# Interaction — Databricks

Interactions follow Du Bois patterns where a Du Bois primitive exists. When building custom, match Du Bois behavior — don't invent new ergonomics.

## The eight states

Every interactive element must handle:

1. **Default** — at rest
2. **Hover** — pointer over (desktop only)
3. **Focus** — keyboard navigation, use `:focus-visible`
4. **Active** — being pressed / held
5. **Disabled** — non-interactive, communicate why
6. **Loading** — action in progress
7. **Error** — action failed, communicate recovery
8. **Success** — action completed

Missing states are P1 findings in `audit`.

## Focus rings

**Never `outline: none`** without replacement. Use `:focus-visible` so keyboard-only users see rings but mouse users don't:

```css
button:focus-visible {
  outline: 2px solid var(--db-accent);
  outline-offset: 2px;
  border-radius: inherit;
}
```

Contrast of focus ring against background must meet 3:1. On Navy 900 surfaces use `var(--db-accent)` at full opacity — it clears contrast.

## Buttons

Three tiers — **primary / secondary / tertiary** — never more. Match Du Bois `Button` variants.

| Tier | Appearance | Use for |
|---|---|---|
| Primary | Filled, `--db-accent` bg, white text, `--db-radius-md` | The single main action per screen |
| Secondary | Outline, `--db-dark` border + text, transparent bg | Supporting actions |
| Tertiary | Text-only, `--db-info` text, underline on hover | Dismiss, inline navigation |

**One primary per screen.** If you have two primary buttons, one of them is a secondary.

## Forms

- `<label>` is **required** on every input. Placeholder is not a label.
- Validate on **blur** (not on every keystroke, not only on submit).
- Error messages appear **below** the field, linked via `aria-describedby`.
- Error text uses `var(--db-error)`. Error border on the input. Don't rely on color alone — add an error icon.
- Required indicators: `*` in the label, not just "required" badge.
- Group related fields with `<fieldset>` + `<legend>`.

## Loading

Choose the right loading pattern:

- **Optimistic update** — for low-stakes mutations. Show success immediately, roll back on failure with a toast. Best for: favoriting, commenting, adding to a list.
- **Skeleton screen** — for initial content loads. Show the shape of what's coming (gray blocks matching final layout). Best for: dashboards, tables, cards.
- **Spinner** — last resort. Use only when you genuinely cannot predict the content shape.
- **Inline spinner on a button** — for submit actions. Disable the button, swap the label area for a 16 px spinner.

Never show a full-screen spinner for more than 500 ms. If the operation is slower, show progressive content.

## Modals

- Use `<dialog>` element or the `inert` attribute on background.
- Only for destructive confirmations, critical decisions, or short wizards (≤ 3 steps).
- If the content is long, use a drawer or a dedicated page.
- Close mechanisms: ESC key, X button top-right, backdrop click (unless destructive). All three must work.
- Focus traps inside, return focus to the trigger on close.

## Dropdowns & popovers

- Use the native `popover` attribute + CSS Anchor Positioning where supported.
- **Never** use `position: absolute` inside an `overflow: hidden` container — the dropdown will clip. Use `position: fixed` or a portal.
- Keyboard support: Arrow keys to navigate items, Enter to select, ESC to close.

## Tables

Matching Du Bois Table conventions:

- Header row: `--db-neutral-100` background, weight 500.
- Row height: 40-48 px. Don't pack rows tighter than 40 px — touch targets.
- Zebra striping: optional, very subtle (`color-mix(in oklch, var(--db-dark) 3%, transparent)`).
- Sortable headers: chevron icon indicates active sort + direction.
- Row hover: subtle background shift, not a border.
- Checkboxes (row selection): 16 px, Du Bois Checkbox primitive.

Numeric columns use `font-variant-numeric: tabular-nums` and align right. Text columns align left.

## Hover

Hover is a **desktop-only** affordance. Never hide critical functionality behind hover — it's invisible on touch.

- Hover change: subtle background tint, underline, or cursor change.
- Avoid hover cards/tooltips that require hovering to see content; that content belongs inline or in a click-triggered popover.

## Empty states

Empty states **teach the interface**. "Nothing here yet" is the weakest possible empty state.

Template:
1. One short sentence explaining what this area is for.
2. Primary action to create the first thing (button).
3. Optionally: link to docs.

Avoid stock illustrations — they're an AI-era cliche. A small Databricks spark mark or a minimal line icon works better.

## Rules

**DO**:
- Handle all eight states.
- `:focus-visible` for focus rings.
- One primary button per screen.
- Validate on blur.
- Skeleton screens for initial content.
- `<dialog>` for modals.

**DO NOT**:
- `outline: none` without replacement.
- Rely on placeholder as label.
- Use ARIA roles you don't need (native semantics beat ARIA).
- Make everything a button — use links for navigation, buttons for actions.
- Hide critical features on touch.
- Show spinners for > 500 ms — switch to skeletons.
- Use empty states as decoration.
