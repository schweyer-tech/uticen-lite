# Procedure header — title-styled name, labeled fields, narrative at the bottom — Design

**Date:** 2026-06-28
**Area:** control plane — Logic Builder procedure section header (`plane/`)
**Status:** approved (brainstorm), pending plan

## Problem

The procedure section header (the `<summary>` of each `<details>` section in `_pipe_cards.html`)
currently renders its inputs as one undifferentiated wrapping flex row: `code · name · assertion ·
thr% · count · ✕ · narrative`. The procedure's **name** reads like just another input, the
**assertion** field is only hinted by a placeholder (no label, no explanation of what an "assertion"
is), and the **narrative** wraps below without clear separation. The user wants the header to read as
a titled card: a prominent editable **title**, clearly labeled fields, and the **narrative** as the
bottom row.

## Decisions (from brainstorming)

- **Name = title-styled, always-editable input + focus pencil.** The name renders large/semibold
  (heading-like, borderless until hover/focus) with a pencil button beside it; clicking the pencil
  just focuses the input. It stays a plain `[data-proc-name]` input serialized into the graph by
  `serializeProcedures()` — NOT a separate display/toggle form with its own endpoint (the procedure
  name has no dedicated route; it autosaves with the graph). This differs from the control-title
  editor (`control_edit.html`, which POSTs to `/controls/{id}/title`) deliberately.
- **Three rows:** (1) title row — collapse caret · color dot · code badge · big name input · pencil ·
  ✕ delete; (2) fields row — **Assertion** label + a ⓘ help tooltip + assertion input, then the
  threshold relabeled **"Fail if [_]% or [_] items"**; (3) narrative row — a **Narrative** label + the
  full-width textarea at the bottom.

## Goal

The procedure header reads as a titled card: a prominent editable name, a labeled Assertion field with
an explanatory tooltip, a readable threshold, and the narrative as the clearly-separated bottom row.

## Non-goals

- **Purely presentational.** No change to the data attributes, the serialized graph shape, the
  view-model (`_procedure_context`/`_card_bands`), any route, the bundle, or `schema_version`.
- No change to the workpaper render, the Flowchart, or the Test node card.
- No new server endpoint for the procedure name (it rides the existing graph autosave).

## Architecture / components

This touches three files; the data attributes (`data-proc-code`, `data-proc-name`, `data-proc-assert`,
`data-proc-pct`, `data-proc-count`, `data-proc-narrative`, `data-proc-del`, `data-proc-head`,
`data-proc-id`) are unchanged. `serializeProcedures()` reads them by attribute (not position), so the
row restructure is serialization-safe.

### Unit 1 — Header markup (`_pipe_cards.html`)

Restructure the `.proc-head` span (inside the section `<summary>`) into three rows. **Leave
`.proc-dot` where it is — a SIBLING of `.proc-head` (directly under `<summary>`), NOT inside
`.proc-head`.** The dot sits outside the `.proc-head` no-toggle guard on purpose: clicking it toggles
the section, and the e2e (`test_builder_collapse_and_section_insert`) drives collapse via
`.proc-dot` click. Moving the dot inside `.proc-head` would silently break that. The summary stays
`[caret] [.proc-dot] [.proc-head column]`.

Inside `.proc-head` (now a column of three rows):

- **Title row** (`.proc-title-row`): `[data-proc-code]` (small code badge) at the left; render
  `[data-proc-name]` as the big title input (`class="proc-in proc-name-title"`,
  `placeholder="Procedure name"`); add a pencil button `[data-proc-name-edit]` (`type="button"`,
  `aria-label="Edit name"`) after the name; keep `[data-proc-del]` (✕) at the right.
- **Fields row** (`.proc-fields-row`): a `<label>` reading **Assertion** + a help affordance
  `<span class="proc-help" tabindex="0" title="…">ⓘ</span>` (native-tooltip via `title`, plus
  `aria-label`), then `[data-proc-assert]`; then the threshold as inline text "Fail if"
  `[data-proc-pct]` "%" "or" `[data-proc-count]` "items".
- **Narrative row** (`.proc-narrative-row`): a `<label>` **Narrative** + the existing
  `[data-proc-narrative]` textarea (full width).

Assertion tooltip copy (the `title` value):
> "The audit assertion this procedure verifies — the specific claim it proves about the control (e.g.
> 'Segregation of duties', 'Authorization', 'Completeness', 'Existence'). Shown as the procedure's
> subtitle in the workpaper."

### Unit 2 — New-section JS template + pencil handler (`logic_builder.html`)

- Mirror the exact 3-row markup (incl. pencil, Assertion label + ⓘ tooltip, threshold labels, Narrative
  label) in `newProcedureSection()`'s innerHTML, so a freshly-added section matches a server-rendered
  one.
- Add a delegated click handler on `#pipe-cards` for `[data-proc-name-edit]`: find the closest
  `[data-proc-head]`'s `[data-proc-name]`, `.focus()` it, and call `e.preventDefault()` +
  `e.stopPropagation()` so the click neither toggles the `<details>` nor does anything else. (The
  existing `.proc-head` summary-typing guard at ~line 637 covers keydown; the pencil adds the click
  affordance.)

### Unit 3 — Styling (`app.css`)

- `.proc-head` becomes a column (`flex-direction: column; align-items: stretch`) holding the three
  rows; each row is its own flex line. Keep `.proc-head` under the `<summary>`.
- **Title input** `.proc-head .proc-name-title` — heading-styled: larger font (≈20px), semibold,
  `var(--font-sans)`, transparent border until `:hover`/`:focus`, flexible width. **Critical (learning
  0032):** qualify the selector so it out-specifies the global `input[type="text"]` block, which is
  declared later in `app.css` — use `.proc-head input.proc-name-title` (or
  `.proc-head .proc-name-title[type="text"]`) so the title font-size is not silently reverted to the
  base 13px. Verify the rendered font-size in a real browser.
- **Pencil** `.proc-name-pencil` — mirror `.control-title-pencil` (round, `::before { content: '✎' }`,
  hover accent), routed through design tokens (learning 0005).
- **ⓘ help** `.proc-help` — small muted circle/icon, `cursor: help`, hover accent; the tooltip is the
  native `title`.
- **Fields/threshold labels** — small muted inline labels; keep `[data-proc-pct]`/`[data-proc-count]`
  compact (≈64px) as today.
- All colors via `var(--token)` (learning 0005); reuse `.proc-in` for the base input chrome where it
  still applies.

## Data flow

Unchanged. The header inputs serialize into `graph.procedures[]` exactly as before
(`serializeProcedures()` reads by `data-proc-*` attribute). The pencil and tooltip are inert with
respect to the graph.

## Error handling

No new failure modes. The pencil handler must `stopPropagation` so it cannot toggle the section; the
title input remains inside `.proc-head` so it inherits the existing no-toggle guard.

## Testing strategy

- **Plane render** (`tests/plane`): the Builder GET for a control with a procedure renders the new
  structure — assert the **Assertion** label text, the tooltip `title` substring (a distinctive phrase
  from the copy), the pencil button `data-proc-name-edit`, the **Narrative** label, and that
  `[data-proc-name]` carries the `proc-name-title` class. The existing band/proc tests must stay green
  (attributes unchanged).
- **Browser e2e** (`tests/e2e -m browser`, learning 0012 — the procedure header form is restructured in
  place): the existing fills of `[data-proc-name]`/`[data-proc-assert]`/`[data-proc-count]`/
  `[data-proc-narrative]` still resolve (unchanged attributes); add a **CSS teeth-check (learning
  0032)** asserting the title input's computed `font-size` is the intended heading size (not the base
  13px), and assert the pencil focuses the name input.
- **Gates:** `python -m pytest -q` pristine; `python -m ruff check .`; `python -m mypy uticen_lite`.

## Global constraints

- Purely presentational: NO change to `contract/bundle.schema.json`, `schema_version`, routes,
  view-models, or the serialized graph shape (learnings 0001, 0015).
- CSS specificity: the title input rule must out-specify the global `input[type="text"]` block
  (learning 0032); verify rendered font-size in a real browser.
- Route every color through `var(--token)`; restyle via component-scoped classes, never by mutating a
  shared base rule (learning 0005).
- The narrative + name inputs stay inside `.proc-head` (inside `<summary>`) so they inherit the
  no-toggle guard; the pencil handler `stopPropagation`s.
- ruff `py311`, line length 100; Python ≥3.11.
