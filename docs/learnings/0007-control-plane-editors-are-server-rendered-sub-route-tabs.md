---
id: 0007
date: 2026-06-20
area: frontend
tags: [plane, fastapi, jinja, routing, ux]
status: active
supersedes: null
superseded_by: null
---

# Model a multi-section control-plane editor as server-rendered GET sub-route tabs sharing a nav include — not client-side JS tabs

## Context

The Edit Source page grew three sections (Definition, Data, History). The plane is FastAPI + Jinja +
HTMX with no bespoke client state. Client-side JS tabs would have added state the rest of the app
doesn't use and broken bookmarking / open-in-new-tab.

## What worked

Each section is its own `GET` sub-route — `/sources/{id}` (Definition), `/sources/{id}/data`,
`/sources/{id}/history` — and every tab template includes one shared `_source_tabs.html` nav, passed an
`active` context key to highlight the current tab. Tabs are real URLs: bookmarkable, openable in a new
tab, and trivially server-rendered. Writing actions inside the tabs still follow learning [[0002]]
(async/writing handlers open their own connection).

## The rule

For a multi-section editor in the control plane, **make each section a distinct server-rendered `GET`
sub-route that shares one `_<thing>_tabs.html` nav include and an `active` context key — do not build
client-side JS tabs.** Register the specific sub-routes (`/{id}/data`, `/{id}/history`) so the `/{id}`
param route cannot shadow them, and keep the section's data-loading in its own handler. This keeps
every section a real, linkable URL and matches the JS-light/HTMX ethos.

## Corollary — drill-down/detail output is a real page (new tab), not an inline below-the-fold drawer

The step-data inspector first shipped as an HTMX drawer swapped into a `#step-drawer` div at the
**bottom** of the long builder page. Clicking a step's row-count appeared to do nothing — the swapped
content rendered below the fold, so the author couldn't tell it had opened. The fix was to make the
row-count a `target="_blank"` link to the existing server-rendered step page (`step_data.html`,
`extends base.html`), opening the data in a **new tab** with a back-to-builder link and same-tab
pagination — the same "real, linkable URL" ethos as the tabs above.

So: render drill-down/detail output (a step's rows, an item's detail) as a **server-rendered page reached
by a real URL** — open it in a new tab (`target="_blank" rel="noopener"`) or navigate to a sub-route —
**not** an inline JS/HTMX panel appended at the bottom of a tall page. An inline drawer below the fold
reads as a no-op; a page (or new tab) is unmistakable, bookmarkable, and back/forward-navigable.

## Reference

- `controlflow_sdk/plane/templates/_source_tabs.html` (shared nav, `active` highlight).
- `controlflow_sdk/plane/routes/sources.py` (`edit_source`, `source_data`, `source_history`).
- Drill-down-as-new-tab: `controlflow_sdk/plane/templates/step_data.html` (full page) + the
  `target="_blank"` row-count link in `partials/_pipe_node.html` / `_pipe_diagram.html`;
  `routes/pipeline.py::step_data`.
- Connection rule for the writing handlers: learning [[0002]].
