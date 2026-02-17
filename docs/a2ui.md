# A2UI Integration Notes

This document describes deepbot's current A2UI runtime behavior and Discord renderer constraints.

## Envelope Processing
- `a2ui` envelopes are processed as a batched list in one response.
- Envelopes are applied in array order.
- `updateDataModel` is replace-only (full replacement).
- `updateComponents` is replace-only (full replacement).
- `deleteSurface` maps to Discord message deletion.
- `createSurface` and update envelopes map to Discord message send/edit.

## Surface State
- State is isolated by `(session_id, surface_id)`.
- `updateDataModel` re-renders from stored template components using current data model.
- `/reset` and successful re-auth clear session surface states.

## Components V2 Renderer Rules
- `LayoutView` is preferred for A2UI components.
- Discord Components V2 does not allow normal message `content` with `LayoutView`.
- Top-level interactive components (`button`, `select`) are wrapped in `ActionRow`.
- `select` is not used as a `Section` accessory; section-contained selects are rendered via `ActionRow`.
- If a button has `url` and no `action`, it is treated as a link button.

## Fallback Behavior
- If send/edit with `view` fails, deepbot retries without `view`.
- Retry path avoids empty-message errors by providing a minimal fallback content when needed.

## Formatting Guidance
- Discord markdown tables are not supported.
- Use bullet lists instead of pipe table syntax (`| ... |`).

