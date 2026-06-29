---
id: 0044
date: 2026-06-29
area: frontend
tags: [frontend, plane, templates, partials, dry, drift]
status: active
supersedes: null
superseded_by: null
---

# Chrome that must stay identical across sibling pages/tabs lives in ONE shared partial (markup + its inline JS) — parallel hand-rolled copies silently drift

## Context

The control editor's three tabs (Definition / Logic / History) each hand-rolled their own
`.page-head` header. When the Definition header was redesigned into an editable-title card
(display + pencil + inline `/title` form) with a **Run** button, only Definition got the new
markup — Logic and History kept a plain `<h1>` + mono id. The tabs visibly disagreed; the
regression was invisible to each template's own diff.

## What went wrong / worked

Parallel copies of "the same" chrome do not stay the same: a redesign lands in one copy and the
siblings fall behind. The fix was to extract the header — **markup AND its title-edit `<script>`** —
into one `partials/_control_header.html` that all three tabs `{% include %}` (caller sets `active`
for the tab highlight). Extracting markup without its inline JS is half a fix; and the JS had to be
**deleted from the origin** template, or the same handler binds twice and the `id` collides.

## The rule

When the same header/toolbar/nav chrome must render identically across sibling surfaces (tabs,
sibling routes), put it in ONE included partial — never N hand-rolled copies that "look the same
today." Move BOTH the markup and any chrome-specific inline `<script>` into the partial, and DELETE
the script from every origin template so a single page can't double-bind a handler or duplicate an
element `id`. Caller-specific bits (the active-tab key, a per-page fallback) are passed in as
context. This is the audit-every-site family ([[0014]], [[0038]]): the cure for "thread the change
through every copy" is to have one copy. Restyle of a shared *structural class* still uses a modifier
class, not a base-rule mutation ([[0005]]).

## Reference

- `uticen_lite/plane/templates/partials/_control_header.html` (the shared header + title-edit JS).
- `control_edit.html`, `logic_builder.html`, `control_history.html` (each `{% include %}`s it,
  setting `active`; the new-control / no-`control` fallbacks stay inline).
- Commit landing PR #110.
