---
id: 0022
date: 2026-06-22
area: store
tags: [migrations, schema-version, tests, store]
status: active
supersedes:
superseded_by:
---

# Bump the STORE `SCHEMA_VERSION` constant in the same commit that appends a migration step — the focused per-step test won't catch the miss

## Context
`store/migrations.py` applies a forward-only `_STEPS` list, setting `PRAGMA user_version`
to the step index. `migrate()` drives off `len(_STEPS)` **dynamically**, but the module
also exports a **separate manual constant** `SCHEMA_VERSION` that several existing tests
assert against (`test_migrations.py`, `test_repo_pipeline.py` use `user_version ==
SCHEMA_VERSION`). This is distinct from the **bundle** `schema_version` ("1.0" in
`schema/__init__.py`), which must stay frozen.

## What went wrong
Appending step 6 (the new `sources.sheet` column + `source_fetch` table) bumped
`user_version` to 6 but left `SCHEMA_VERSION = 5`. The task's focused test asserted only
`user_version >= 6` (passed), so the break was invisible at the task gate — 5 existing
store tests then failed `assert 6 == 5`, surfacing only when the broader store suite ran.

## The rule
When you append a step to `_STEPS` in `store/migrations.py`, **bump the STORE
`SCHEMA_VERSION` constant in the same commit**, and update any test that hardcodes the old
integer literal. Do NOT touch the bundle `schema_version` in `schema/__init__.py` (frozen
by the cardinal rule [[0001]]). A per-step test that asserts `user_version >= N` is not
sufficient — run the **whole** `tests/store` suite before the task gate, because the
constant-equality assertions live in sibling tests, not the new one.

## Reference
- `uticen_lite/store/migrations.py` (`_STEPS`, `migrate`, `SCHEMA_VERSION`)
- `tests/store/test_migrations.py`, `tests/store/test_repo_pipeline.py` (the `== SCHEMA_VERSION` asserts)
- Cardinal-rule contrast: [[0001]] (bundle `schema_version` stays frozen; only the store version moves)
</content>
