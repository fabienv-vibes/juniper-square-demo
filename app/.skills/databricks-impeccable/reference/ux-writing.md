# UX writing — Databricks

Databricks has a specific editorial voice. Adopt it for every button label, empty state, error message, docs heading, and UI string produced through this plugin.

## Voice

- **Plain**. Prefer the direct word over the flowery one.
- **Specific**. Numbers, names, dates, concrete nouns over abstractions.
- **Active**. "Save changes" not "Changes will be saved."
- **Second person** for product UI. "You can" not "The user can."
- **Third person** for reference docs.
- **Confident**. Short declaratives. No hedging ("might", "could potentially").

## Words to avoid

Banned marketing words. These flag as P1 in `audit` and `critique`:

- **Superlatives**: pivotal, critical, key, significant, strong, powerful, unlock, transform, seamless, effortless, robust, cutting-edge, best-in-class, enterprise-grade.
- **Slide-deck framing**: strategic frame, expansion path, core narrative, force multiplier, beachhead, synergy, leverage (as verb), ideate.
- **AI-ism**: dive in, let's explore, fascinating, wonderful, delighted to, I understand your concern.
- **Soft hedges**: "simply", "just", "easily" — they reassure the writer, not the reader.

## Words to prefer

| Instead of | Use |
|---|---|
| "Leverage our platform" | "Use Databricks" |
| "Unlock insights" | "Query the data" |
| "Seamlessly integrate" | "Connect" |
| "Simply click" | "Click" |
| "Cutting-edge" | (remove — state the actual capability) |
| "Robust" | (remove — state the specific guarantee) |
| "Powerful" | (remove — state what it does) |
| "Best-in-class" | (remove — not factual) |

## Headings

Headings are **labels**, not taglines.

- **Good**: "Workspace usage", "Failed jobs", "Get started", "Set up a cluster"
- **Bad**: "Unleashing the power of your workspace", "Your path to production", "Welcome to the future of data"

Sentence case, not title case. "Connect a data source" not "Connect A Data Source".

## Buttons

- **One action**, imperative verb. "Run", "Save", "Connect", "Delete".
- If destructive, say what will be destroyed: "Delete workspace" not "Delete".
- Avoid "OK", "Submit", "Click here".
- Secondary buttons: "Cancel", "Back" — not "Nevermind", "Go back".

## Errors

Errors explain what happened, why, and what to do next:

- **Good**: "Cluster couldn't start — out of capacity in us-west-2. Try a different region or wait 2 minutes."
- **Bad**: "An error occurred. Please try again."

## Empty states

See `interaction-design.md`. Empty state copy template:

1. What this area is for (one sentence).
2. Call-to-action to create the first item.
3. Optional: link to relevant docs.

Good: "No jobs yet. Create a job to run notebooks on a schedule. [Docs]"
Bad: "Nothing here yet!"

## Docs

- Start with the task, not the feature. "To schedule a notebook" not "The scheduler feature".
- One task per page.
- Use the imperative. "Click" not "You would click".
- Show code early, explain after.
- Never apologize for complexity. Explain it plainly.

## Uncertainty

When something is unknown, **say so**.

- **Good**: "Not yet confirmed", "Unknown — check with support", "We don't measure this".
- **Bad**: "It may or may not", "This might potentially", "Arguably".

## Rules

**DO**:
- Plain statements.
- Second person, active voice.
- Sentence case for headings and buttons.
- Imperative verbs for actions.
- Acknowledge uncertainty directly.

**DO NOT**:
- Use the banned words list.
- Write taglines as headers.
- Apologize or hedge.
- Repeat information the UI already shows.
- Use emoji as emphasis (unless the user explicitly asked).
- Write "please" or "kindly" in UI — it sounds servile.
