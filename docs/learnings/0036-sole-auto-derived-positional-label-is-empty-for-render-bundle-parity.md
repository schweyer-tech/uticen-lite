---
id: 0036
date: 2026-06-28
area: contract
tags: [render, bundle, procedures, byte-identity, parity]
status: active
supersedes: null
superseded_by: null
---

# Auto-derive a positional label (P1..Pn) only when ≥2 items exist; a SOLE auto-derived item carries an EMPTY label so the render stays byte-identical and matches the bundle's single-item default

## Context

Procedures carry a `code` (P1, P2, …). A legacy control with no defined procedures derives
ONE auto procedure at read/run time. The workpaper heading keys off `if proc.code:` —
`{code} · {title}` when a code is present, else the historical `P{i}: {title}`. The bundle's
single-procedure assemble path hardcodes `code=""`.

## What went wrong

- Assigning the lone auto procedure `code="P1"` changed the single-procedure workpaper
  heading from `P1: {title}` to `P1 · {title}` — silently altering existing single-control
  output and breaking the "N≤1 byte-identical" guarantee.
- It also made the **local render** (code `"P1"`) disagree with the **exported bundle**
  (single-procedure path emits `code=""`): the audit file the app shows would differ from the
  workpaper rendered locally for the same control.
- No test pinned single-procedure heading byte-identity, so the suite stayed green.

## The rule

- When auto-deriving a **positional** label (P1..Pn) for a grouping that is **also serialized
  into the bundle**, assign the positional label only when **≥2** items exist; a **sole**
  auto-derived item gets an **empty** label. This keeps the single-item render byte-identical
  to the pre-feature form AND matches the bundle's single-item default (empty label).
- Author-**defined** labels always keep their own value (only the AUTO path, and only when it
  is the lone item, is forced empty).
- A lone auto-numbered label silently changes existing single-item output and makes the local
  render disagree with the exported bundle — pin it with a test asserting the sole item's
  label is empty AND the single-item render uses the legacy (no-prefix) form.

**Corollary — enforce the sole-empty rule at the AUTHORING surface too, not only at
render/bundle.** When a Builder/editor renders a per-item HEADER for a grouping whose count can
be 1 or N, the **display pre-fill default** AND the **client serialize default** must each keep
a sole auto-item's label empty, and the two must AGREE. A header that pre-fills the lone item's
code as `"P1"` (e.g. `code = p.code or f"P{i+1}"`) and a serializer that defaults an empty code
to `"P{i+1}"` will, on the first open-and-save, **promote** the single-item control to an
explicit `code="P1"` — flipping its workpaper heading and re-introducing the exact local-vs-bundle
divergence the render-layer rule prevents (the render guard alone is not enough once an authoring
surface can write the label). Make both defaults conditional on `count > 1` (2026-06-28: fixed
`_procedure_context`'s display default and `serializeProcedures()`'s persist default together;
pinned with a view-model test asserting a sole procedure's `code == ""` and a 2+ set is
`P1..Pn`). Author-typed labels are always preserved at any count.

## Reference

- `uticen_lite/pipeline/procedures.py` — `_auto_procedure` / `effective_procedures`
  (the `lone` branch that forces `code=""`).
- `uticen_lite/render/html.py` — `_emit_procedures` (`if proc.code:` heading branch);
  `uticen_lite/bundle/assemble.py` — single-procedure path (`code=""`).
- `uticen_lite/plane/routes/pipeline.py` `_procedure_context` (display default) +
  `uticen_lite/plane/templates/logic_builder.html` `serializeProcedures()` (persist
  default) — the two authoring-surface defaults that must agree (the 2026-06-28 corollary).
- Keeps the bundle and the local workpaper in parity ([[0001]] cardinal); verdict/threshold
  stay out of the bundle ([[0015]]).
