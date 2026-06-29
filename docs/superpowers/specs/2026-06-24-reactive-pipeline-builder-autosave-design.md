---
id: reactive-pipeline-builder-autosave
date: 2026-06-24
area: frontend
tags: [plane, htmx, ux, autosave]
status: approved for planning
---

# Make pipeline-builder edits reactive and scroll-stable

## Problem

The pipeline builder currently handles several common edit actions by serializing the whole graph and submitting the entire form. That causes a full page navigation/re-render, which snaps the user back to the top of the page. The worst cases are:

- adding a filter condition
- adding narrative text
- removing a node
- other small card-level edits inside the builder

This makes the builder feel jumpy instead of reactive.

## Decisions locked during brainstorming

| Question | Decision |
| --- | --- |
| Scope | All pipeline-builder edits that currently trigger a full submit |
| Save model | Auto-save on each edit |
| UX goal | Keep the user in place; update only the edited region |

## Proposed design

### Architecture

Keep the server-rendered pipeline builder and the store-backed graph as the source of truth, but replace full-page form submission for in-place edits with an async save helper. The helper will serialize the current graph, POST it to the existing builder save endpoint, and update only the pipeline cards fragment from the response.

The page should no longer navigate on routine edits. Instead, the edited card stays visible, the new/changed row appears in place, and the browser scroll position is preserved.

### Components

1. **Shared save helper in the builder template** — one client-side function used by add-condition, narrative changes, remove-node, and any similar card edits.
2. **Partial save response from the builder route** — on successful autosave, return the re-rendered cards fragment rather than forcing a redirect.
3. **Small UI status affordance** — a subtle saved/saving/error state so the user can tell the autosave is working.

### Data flow

1. User edits a card.
2. The builder serializes the current graph state.
3. The browser sends the graph to the existing builder POST route.
4. The server validates and persists the graph.
5. The server returns the updated cards fragment.
6. The client swaps only the cards region and keeps the current viewport.

### Error handling

If validation fails, keep the edited cards visible and show the same inline error state the builder already uses for rejected graphs. Do not fall back to a redirect on routine autosave failures. The user should be able to correct the error without losing their place.

### Testing

- Add a focused regression test for the builder save path so a card-level edit returns an in-place response instead of a full navigation.
- Add or update browser smoke coverage for the builder edit flow to confirm the page stays on the same scroll position after adding a condition or narrative change.
- Verify existing builder validation still renders inline errors for invalid graphs.

## Result

This keeps the pipeline builder feeling local and reactive while preserving the existing server-side graph model and validation logic.
