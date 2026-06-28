---
id: 0032
date: 2026-06-28
area: frontend
tags: [plane, web, css, specificity, forms]
status: active
supersedes: null
superseded_by: null
---

# Qualify a component-scoped input/textarea style rule with its `[type=...]` attribute (or a second class) so it out-specifies the global `input[type="text"]` base rule — a bare `.component input` ties and loses on source order

## Context

`controlflow_sdk/plane/static/app.css` has a global form block that styles fields by
element + attribute:

```css
input[type="text"], input[type="number"], input[type="file"], textarea, select {
  font-size: 13px; padding: 8px 10px; margin-top: 6px; ...
}
```

A component rule written as `.control-title-edit-form input { font-size: 24px; ... }`
has specificity **(0,1,1)** — one class + one element. The base `input[type="text"]`
selector is **also (0,1,1)** — one element + one attribute. On a specificity tie the
later-declared rule wins, and the base block is declared *after* the component block, so
the field silently reverts to `font-size: 13px` (and the base padding/margin). The
control-title inline editor shipped this way: its intended 24px/600 styling existed but
never applied, so "edit title" rendered a tiny 13px box instead of in-place editing.

## What went wrong

The bug is invisible in the CSS source — both rules are present and look correct; only the
cascade tie-break (source order) reveals which one applies. A glance at the file reads as
"the title input is styled to 24px," but the rendered result is 13px.

## The rule

- When a component styles an `<input>`/`<textarea>`/`<select>` AND the global
  `input[type="..."]`/`textarea`/`select` block in `app.css` sets the property you are
  overriding, **qualify the component selector so it out-specifies that base rule** — add
  the same attribute (`.component input[type="text"]` → (0,2,1)) or a dedicated class.
  A bare `.component input` is (0,1,1): it ties the base and loses because the base block
  comes later in the file.
- **Do not** "fix" it by reordering rules or by weakening/mutating the shared base block
  (that re-exposes every other field — see [[0005]]). Raise the *component* selector's
  specificity instead.
- When you intend a styled field to read as inline/borderless (e.g. an in-place title
  editor), verify in a real browser, not by reading the rule — the cascade tie is silent.

## Reference

- `controlflow_sdk/plane/static/app.css` — the global `input[type="text"], …, textarea,
  select` block and the `.control-title-edit-form input[type="text"]` rule that must
  out-specify it.
- PR #96 (commit `1ce5961`).
- Related selector-discipline rule: [[0005]].
