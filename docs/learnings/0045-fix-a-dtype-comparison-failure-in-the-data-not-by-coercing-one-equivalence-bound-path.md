---
id: 0045
date: 2026-06-29
area: testing
tags: [equivalence, rule_spec, dtype, control-plane, test-seeding, generated-code]
status: active
supersedes:
superseded_by:
---

# Fix a dtype/comparison failure in the DATA (the column's declared `data_type`), never by coercing only ONE of two equivalence-bound code paths

## Context

The single-source-trace cycle (#29) needed a numeric Test condition (`amount gt 100`) over a
control-plane source. The route test seeded the source's key via `repo.set_columns(is_key=True)`
directly. Two things silently broke, and an implementer "fixed" them in production code:

- The loader derives `Population.key_columns` from the source's **`key_config`**, not from the
  per-column `is_key` flags. `repo.set_columns` writes the flags but NOT `key_config`, so the key
  never resolved → a key_config "reconciliation" block was added to `_load_source_populations`.
- The column stayed **text**-typed (upload defaults to `data_type: "text"`), so `col > 100` (int
  vs text) raised in the compiled/materialize path → `_mask_expr` was changed to wrap gt/ge/lt/le
  with `pd.to_numeric(col, errors='coerce')` **only in the generated path**, leaving the
  interpreter `_condition_mask` untouched.

The second change is the dangerous one: `_mask_expr`-generated `test()` and `_condition_mask`
are bound by an equivalence invariant ([[0009]]). Coercing one but not the other **diverges**
them; the divergence only shows on a text-dtype numeric comparison, which the equivalence
fixtures didn't cover — so the whole suite stayed green while the contract silently broke.

## What went wrong

A production code path (generated-code semantics, bundle-adjacent) was edited to paper over an
**unfaithful test seed**. Root cause was the seed, not the code.

## The rule

- When two paths are bound by an equivalence invariant ([[0009]]: the `_mask_expr`-generated
  `test()` must equal the `_condition_mask` interpreter), **NEVER** resolve a dtype/comparison
  failure by coercing in one path only. A green suite does not prove equivalence held — the
  equivalence fixtures may not cover the case. **Fix the DATA**: set the source column's
  `data_type` so both paths see the same dtype (ordering ops on a text column are *meant* to
  raise — that is deliberate author feedback, [[0011]] — not a thing to coerce away in the
  generated path).
- **Seed a control-plane source through the real `POST /sources/{id}` save route**, not
  `repo.set_columns`. The save route writes the per-column flags AND `key_config` AND
  `data_type` together (as a real author does); `repo.set_columns` alone sets the `is_key` flag
  but leaves `key_config` empty (so `Population.key_columns` resolves to nothing) and the column
  text-typed (so numeric comparisons fail/raise downstream). An unfaithful seed tempts a
  production-code workaround — make the seed faithful instead.
- A render-only feature that finds it must touch `rules/render_rule.py`, the bundle schema, or
  the store schema to make a test pass is a signal the **test** is wrong, not the production
  code — stop and re-seed.

## Reference

- `uticen_lite/rules/render_rule.py` (`_mask_expr`, generated) and `uticen_lite/rules/evaluate.py`
  (`_condition_mask`, interpreter) — the two equivalence-bound paths.
- `uticen_lite/plane/routes/sources.py` (`save_source`, `POST /sources/{id}`) — sets columns +
  `key_config` + `data_type` together; `uticen_lite/store/loader.py` (`_binding`) derives keys
  from `key_config`.
- `tests/plane/test_logic_trace.py` (`_configure_source`) — the faithful seed via the save route.
- Kin: [[0009]] (generated == interpreter), [[0011]] (match condition value type to the column's
  loaded dtype).
