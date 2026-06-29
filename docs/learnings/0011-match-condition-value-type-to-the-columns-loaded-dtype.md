---
id: 0011
date: 2026-06-20
area: data-integrity
tags: [rules, coercion, dtype, authoring, testing]
status: active
supersedes: null
superseded_by: null
---

# Match a rule condition's value type to the bound column's loaded dtype — a bool/number literal silently matches nothing against a string column

## Context

While grounding the control-plane browser smoke test (#13), a `can_create eq true` rule produced **zero
violations** against a CSV whose `can_create` column was loaded as text. The control form coerces the
typed value `'true'` → Python `bool True` (`plane/routes/controls.py::_typed`), but the column loaded as
dtype `str` because no source type-mapping was applied — so `'true' == True` is `False` for every row.
This is the same silently-empty-result failure mode #9's coercion-health check exists to surface.

## What went wrong / what worked

The empty result has **no error** — exactly the trap #9 was built to expose. Fixes: apply the source's
type mapping first (so the column coerces to bool/number before evaluation), or write the literal in the
column's loaded type, or use a dtype-agnostic operator. The #13 test sidestepped the ambiguity with a
discriminating fixture (`user_id eq U1` AND `can_create not_empty`) rather than relying on a bool literal
matching a string column.

## The rule

A rule condition compares the **form-coerced** value (bool/int via `_typed`) against the source column's
**loaded dtype**. If the column is loaded as text (no type mapping applied), a bool or numeric literal
matches nothing and yields a silently-empty result with no error. When authoring or testing a condition
over a typed column, **ensure the source type mapping is applied first** so the column coerces, or pick a
dtype-agnostic operator (`not_empty`/`is_empty`/`regex`). Run #9's coercion-health check to catch the
"non-text column coerced to all-empty" case before it becomes a wrong result. Same silently-empty class
as [[0004]].

## Reference

- `uticen_lite/plane/routes/controls.py` (`_typed` — form value coercion).
- `uticen_lite/plane/coercion_check.py` (#9 coercion-health check that flags the all-empty case).
- `uticen_lite/rules/evaluate.py` (where the column/value comparison runs).
- Related silently-empty class: [[0004]].
