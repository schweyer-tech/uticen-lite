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

## Reference

- `controlflow_sdk/plane/templates/_source_tabs.html` (shared nav, `active` highlight).
- `controlflow_sdk/plane/routes/sources.py` (`edit_source`, `source_data`, `source_history`).
- Connection rule for the writing handlers: learning [[0002]].
