---
id: 0038
date: 2026-06-28
area: frontend
tags: [control-plane, htmx, templates, render-context]
status: active
supersedes: null
superseded_by: null
---

# When a template partial is rendered from more than one endpoint, thread every new context key through ALL of its render sites — and centralize that context in one builder so the sites can't drift

## Context

`partials/_pipe_cards.html` is rendered from FOUR places: the full-page Builder GET (via
`_editor_context`), the autosave-success POST, the autosave-error 422 POST, and the AI-apply
POST. Adding the new `bands` grouping required every one of those contexts to supply `bands`. A
key added only to the full-page context would render correctly on load and then **lose the
feature after the first HTMX swap** that re-renders the partial from a site that forgot the key.

## What went wrong

- A context key added to a multiply-rendered partial at only *some* sites passes a full-page
  render test and an isolated partial-render unit test, yet silently drops the feature after any
  HTMX swap whose handler omitted the key — invisible to diff review (a missing site is an
  *absence*, not a diff line) and to load-time checks.

## The rule

Before adding a key to a Jinja partial, `grep` every `TemplateResponse(..., "<partial>.html",
...)` plus every page that `{% include %}`s it, and confirm EACH render site supplies the new
key — the full-page GET **and** every HTMX swap handler that re-renders the partial. Prefer to
**centralize the partial's context in one builder function** (e.g. `_card_bands(...)` beside
`_procedure_context(...)`) that every site calls, so a new field is added in one place and the
sites cannot drift. This is the render-context member of the audit-every-site family — same
shape as threading a column through every SQL writer ([[0023]]) and a value through every
positional consumer ([[0014]]). Pair it with the e2e gate ([[0012]]): an HTMX swap that drops a
key is exactly what the post-swap browser smoke catches.

## Reference

- `uticen_lite/plane/routes/pipeline.py` — `_card_bands` + its four call sites that render
  `partials/_pipe_cards.html` (`_editor_context`, autosave-success, autosave-error 422, AI-apply).
- Same audit-every-site family: [[0014]] (plural accessor call sites), [[0023]] (upsert writers).
