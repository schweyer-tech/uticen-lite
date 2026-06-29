---
id: 0023
date: 2026-06-22
area: store
tags: [upsert, sqlite, on-conflict, regression, call-site-audit]
status: active
supersedes:
superseded_by:
---

# A new column on an `ON CONFLICT DO UPDATE SET col=excluded.col` upsert silently NULLs on every writer that omits the kwarg — thread it through ALL write paths and test survival-across-update

## Context
`repo.upsert_source` uses `INSERT ... ON CONFLICT(id) DO UPDATE SET ... sheet=excluded.sheet`.
Because `sheet` defaults to `None` in the function signature, **any** caller that omits
`sheet=` writes NULL into the row on the UPDATE branch — not "leave unchanged". The same
trap applies to every `col=excluded.col` upsert in `store/repo.py`.

## What went wrong
The new `sources.sheet` column was threaded into the create path and read correctly at
run time, and the headline create→run test passed. But `confirm_refresh` and `save_source`
both call `upsert_source` **without** `sheet=`, so a file-refresh-confirm or a metadata
save silently reset the source to sheet 0 — re-introducing the exact sheet-0 bug the
feature set out to fix. No per-task test combined the new column with the refresh/save
paths, so only the whole-branch review caught it.

## The rule
When you add a column to a table whose upsert does `col=excluded.col`, **audit every call
site of that upsert** and pass the value through on each one — especially the
refresh/confirm/save paths, not just create. Add a regression test that the value
**survives an UPDATE** (create → mutate via another writer → re-read and assert it is
still set), because a create-only test passes while every update writer nulls the column.
This is the write-path analogue of [[0014]] (audit every call site when a model gains a
field) and pairs with the store-only-state discipline in [[0006]].

## Reference
- `uticen_lite/store/repo.py` (`upsert_source`, the `sheet=excluded.sheet` clause)
- `uticen_lite/plane/routes/sources.py` (`confirm_refresh`, `save_source` — the two writers that nulled it)
- `tests/plane/test_sources_multiformat.py` (the survives-an-update regression tests)
</content>
