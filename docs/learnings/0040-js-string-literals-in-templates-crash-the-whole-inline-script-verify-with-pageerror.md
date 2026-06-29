---
id: 0040
date: 2026-06-28
area: frontend
tags: [plane, javascript, templates, escaping, e2e, inline-script]
status: active
supersedes: null
superseded_by: null
---

# A bad escape in a JS string literal emitted from a server template crashes the ENTIRE inline `<script>` (killing every delegated handler) and is invisible to diff review — verify with a parser/`page.on('pageerror')`, and keep human prose OUT of hand-concatenated JS

## Context

`logic_builder.html` builds DOM by concatenating JS string literals inside an inline
`<script>` (e.g. `newProcedureSection()`'s `innerHTML`). A tooltip's `title` carried
human prose with apostrophes/quoted examples; the single quotes inside the single-quoted JS
string were escaped as `\\'` (transcribed verbatim from the plan's markdown code block).

## What went wrong

- In the rendered `.html`, `\\'` is `\\` (an escaped backslash → one literal `\`) **plus**
  `'` (which **closes** the JS string) → the next token is a bare identifier
  (`Segregation`) → **SyntaxError**. A single parse error takes down the **whole inline
  `<script>`**, so `bindCards()` and EVERY delegated handler it registers (insert,
  proc-add, proc-del, the pencil, collapse-restore, autosave) silently stop working.
- It was **invisible to diff review**: the per-task reviewer read `\\'` in the unified
  diff and rationalized it as a valid `\'` in source (diffs do not escape backslashes, so
  `\\'` in the diff IS `\\'` in the file). The plan's markdown snippet had the same `\\'`,
  so the bug was authored, transcribed, and approved without anyone running the script.
- Only the **browser e2e caught it**: a `page.on('pageerror')` listener surfaced
  `Unexpected identifier 'Segregation'`; every header interaction was dead until fixed
  (`\\'` → `\'`).

## The rule

When you emit a **JavaScript string literal from a server-side template** (Jinja/HTML),
especially when building DOM by string concatenation in an inline `<script>`:

1. **Never trust diff review or a markdown snippet for backslash escaping** — a literal
   single quote inside a `'`-delimited JS string is `\'` (ONE backslash); `\\'` is
   backslash-then-string-close and crashes the parse. The reviewer and the plan both
   mis-read it; the only reliable check is to **run a parser**: `node --check` the
   extracted concatenation, or — better, because it proves the assembled page — assert
   **zero JS errors in the e2e** by registering `page.on('pageerror', …)` (the control
   plane's browser gate is the load-bearing check, [[0012]]; "run the real thing" beats
   source inspection, [[0009]]).
2. **Prefer not hand-escaping at all.** Build DOM with
   `document.createElement`/`textContent`/a cloned `<template>` instead of concatenated
   `innerHTML`, OR keep human-authored prose (apostrophes, quotes, em-dashes) **out of the
   JS string entirely** — put it in a `data-*` attribute, a hidden element, or the
   server-rendered partial, and have the JS read it. A `title`/label/tooltip string is
   exactly the kind of prose that should live in HTML, not a JS literal.
3. A server-rendered partial and its JS-injected twin (here `_pipe_cards.html` vs
   `newProcedureSection()`) must stay byte-for-byte equivalent in **structure and
   escaping** — the JS copy is the one that bites, so parse-check it.

## Corollary (2026-06-29) — the two render sites are coupled by any whole-PAGE assertion

A procedure-header **redesign** proved the twins are inseparable: a `tests/plane` unit
test `assert "Fail if" not in page` (reading the FULL rendered page text) FAILS unless
**both** sites change, because the inline `<script>` source IS part of the page text.
Consequences:

- A whole-page `assert "<retired-literal>" not in page` is a cheap, reliable **cross-site
  drift guard** — keep or add one when you remove a literal from a partial that also has a
  JS-string-builder twin; it fails loudly if either site still emits the old markup.
- **When planning**, scope BOTH render sites into the **same task** — never split "change
  the template" and "change the JS builder" across tasks. A whole-page test drags the JS
  builder forward into the first task anyway, so a split mis-sizes the work (observed: a
  3-task plan's Task 1 had to absorb Task 2's JS-builder change to go green).
- Factor shared human prose **once** — a Jinja `{% set ASSERTION_HELP = "…" %}` reused by
  the template, with the **same literal text** in the JS string — so the two copies can't
  drift in wording (and keep that prose apostrophe-free per rule 2 above).

## Reference

- `uticen_lite/plane/templates/logic_builder.html` — `newProcedureSection()` inline
  `<script>` DOM-by-concatenation; the tooltip `title` whose `\\'`→`\'` fix unbroke it.
- `uticen_lite/plane/templates/partials/_pipe_cards.html` — the server-rendered twin
  (plain single quotes; no JS escaping needed).
- `tests/e2e/test_smoke.py` — the browser gate that caught the crash; add a
  `page.on('pageerror')` assertion for inline-script changes.
- Same spirit as [[0009]] (a gate that runs the real thing catches what unit/diff checks
  miss) and [[0012]] (the control-plane e2e is load-bearing for `plane/` DOM/JS changes).
