# Procedure-header card redesign (Logic Builder)

Date: 2026-06-29
Status: approved (brainstorm) — ready for plan
Area: `plane/` (control-plane authoring UI). No bundle/contract surface touched.

## Problem

The procedure-header card in the Logic Builder — the `<summary>` of each collapsible
procedure section, which doubles as the procedure editor — reads as a loose pile of
form boxes rather than a designed object:

- No hierarchy or grouping: code badge, name, pencil, ✕, assertion, two threshold
  inputs, and narrative are all similar-weight bordered boxes.
- The threshold is a cryptic run-on of disconnected inputs: `Fail if [—] % or [—] items`
  — cramped and ambiguous about what trips it.
- Ragged alignment: labels sit inline on some rows, above on others; the narrative
  competes with the structured fields; a floating color dot, caret, badge, pencil, and ✕
  are a lot of small chrome fighting for attention.

## Goal & scope

Restructure the layout **and** polish it. Keep **every** field and behavior — just
regroup, relabel, and align. (Chosen over "visual polish only" and "rethink the whole
interaction".)

**In scope:** the procedure-header `<summary>` markup + its CSS, in **both** render
sites (server template and the JS "Add procedure" builder), plus test/e2e updates.

**Out of scope (non-goals):**
- No change to fields, save payload, or threshold semantics.
- No change to any `data-proc-*` hook, the save/serialize path, or the bundle/contract
  (learning 0015 — thresholds are render+store only; never serialized).
- No de-duplication of the template-vs-JS-builder duplication beyond keeping them in
  strict agreement (a larger refactor; note it, don't do it here).
- No change to the shared "Inputs & shared steps" band, the Test/step node cards
  (`_pipe_node.html`), or the collapse behavior.

## The design — a 3-tier card

Replace the current single `.proc-head` column with three tiers inside the `<summary>`:

1. **Identity bar** — `caret · code chip (P2) · name (big, inline-edit) · pencil · ✕`.
   - The per-procedure color moves from the floating `.proc-dot` to a **3px left border
     stripe** on the whole `.proc-section`/`.band-inputs` card. The dot is removed.
   - The code becomes a real **chip** (monospace, procedure-color-tinted) instead of a
     bare bordered input box. It remains an editable `[data-proc-code]` input, styled as a
     chip.
   - Pencil and ✕ become quiet icon buttons (transparent; border/color wake on hover).
2. **Settings strip** — one top-aligned flex row (wraps on narrow widths):
   - **Assertion** group grows to fill: peer label `Assertion ⓘ` (help tooltip kept) +
     the `[data-proc-assert]` input.
   - **Tolerance** group: peer label `Tolerance` (same `.lbl` style as Assertion and
     Narrative — no surrounding box), then a single control row
     `≤ [pct] % or [count] items · blank = zero`. Inputs (`[data-proc-pct]`,
     `[data-proc-count]`) are **always visible**; the `· blank = zero` hint trails the
     inputs so the label stays clean. The two rows top-align so all three labels share one
     baseline.
3. **Narrative** — full-width labeled `[data-proc-narrative]` textarea, slim (rows=2),
   always visible (not hidden behind a toggle).

All three field labels (`Assertion`, `Tolerance`, `Narrative`) use one shared label
treatment so their whitespace lines up.

### Tolerance semantics (unchanged, just clearer)

A procedure passes when exception-rate ≤ pct **AND** count ≤ failure_threshold_count
(each ignored when blank); both blank → implicit zero-tolerance (any exception is a
deficiency). The `≤ … % or … items` wording with the `· blank = zero` hint conveys this
without changing `model/control.py`.

## The two render sites — must agree

The header markup is produced in two places; **both** change, kept byte-consistent
(learnings 0038, 0036 corollary):

1. `controlflow_sdk/plane/templates/partials/_pipe_cards.html` — the server-rendered
   `<summary>` (Jinja), used on page load and every HTMX `#pipe-cards` swap.
2. `controlflow_sdk/plane/templates/logic_builder.html` → `newProcedureSection(pid, code)`
   — the client JS string builder for "＋ Add procedure".

**0040 hazard:** the JS builder hand-concatenates the assertion-help tooltip prose, which
currently contains escaped apostrophes (`\'Segregation of duties\'`). A bad escape there
silently kills the entire inline `<script>`. Mitigation: **reword the tooltip to contain
no apostrophes** (e.g. drop the quotes around the examples) in *both* sites so they match,
removing the escaping hazard from the hand-built string. Verify with a zero-`pageerror`
e2e assertion after clicking "＋ Add procedure".

## Preserved hooks (do not rename/move out of `[data-proc-head]`)

`serializeProcedures()` reads strictly by attribute, not by structural class:
`[data-proc-head]`, `data-proc-id`, `[data-proc-code]`, `[data-proc-name]`,
`[data-proc-assert]`, `[data-proc-narrative]`, `[data-proc-pct]`, `[data-proc-count]`,
plus `[data-proc-name-edit]` (pencil) and `[data-proc-del]` (delete). Restructuring the
surrounding `<div>`s is safe **iff** every one of these stays present inside
`[data-proc-head]`. The structural classes being removed/renamed (`proc-title-row`,
`proc-fields-row`, `proc-narrative-row`, `proc-threshold`, `proc-dot`,
`proc-code-badge`, `proc-name-title`) are not read by JS.

## CSS approach (`plane/static/app.css`)

- Rework the `.proc-*` block (≈ lines 700–799). Route every color through a `var(--token)`
  (learning 0005); the dynamic procedure color is an inline `border-left` on the card.
- Keep the `input.proc-name-title` specificity trick (selector `(0,2,1)`) so the name field
  beats the global `input[type="text"]` block (learning 0032). Apply the same qualification
  to any new component input rule that must out-specify the base block.
- Both light and dark themes must hold (verified in a real browser, per 0005).

## Testing strategy

Driven by the learnings (run-the-real-thing; teeth checks):

- **Round-trip (server, unit/integration):** in `tests/plane/test_procedures_panel.py`
  (and `test_logic_bands.py` as relevant), assert the new markup still renders
  `data-proc-head`/`data-proc-id`/the name value, and that a save reads back
  code/name/assertion/narrative/pct/count unchanged. Update any assertion that pinned the
  **old** structure or the old "Fail if … % or … items" label text; migrate it to the new
  `Tolerance` label / structure (learning 0012 corollary, 0031 fan-out — grep the whole
  `tests/` tree for `Fail if`, `proc-dot`, `proc-title-row`, `proc-fields-row`,
  `proc-threshold`, `proc-field-label`).
- **e2e browser (`pytest tests/e2e -m browser`):** `test_multi_procedure.py` and
  `test_smoke.py` bind by `data-proc-*` (should still pass). Add:
  - a **zero-`page.on("pageerror")`** assertion across the add-procedure flow (0040);
  - a **computed-style teeth-check** that the name input renders at its intended size
    (~19–20px) — the cascade tie is invisible in source (0032 corollary);
  - an assertion that the **`Tolerance` peer label** is present and the threshold inputs
    (`data-proc-pct`/`data-proc-count`) round-trip a value through a save (0037 — wait on
    the app's own write/save, never inject state).
- **Both add paths covered:** server-rendered procedures AND a JS-added procedure must
  produce identical structure (assert both, per 0038/0014).
- **Visual:** confirm light + dark in a real browser before finishing.
- Keep `python -m pytest -q` pristine, `ruff` + `mypy` green.

## Acceptance

- The card renders as the approved 3-tier layout in both themes; labels line up.
- Every field/behavior preserved; save payload byte-identical for an unchanged procedure.
- Server template and JS builder produce the same structure; no inline-script `pageerror`.
- Full suite + e2e green; ruff/mypy clean; no bundle/contract change.
