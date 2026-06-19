---
id: 0004
date: 2026-06-19
area: data-integrity
tags: [ordering, sql, fixtures, code-review, seams, testing]
status: active
supersedes: null
superseded_by: null
---

# When a query's sort order changes, audit every positional consumer (`x[-1]`/`x[0]`) — single-record fixtures hide ordering bugs

## What went wrong

The control plane's run-history query was written newest-first for the UI
(`store/repo.py:list_runs_for` → `ORDER BY executed_at DESC`). A downstream bundle builder still
selected the "latest" run with `runs[-1]` (`bundle/assemble.py:_build_workpaper`), which was correct
only under the old append-only chronological (oldest→newest) ordering. Result: a control with 2+ runs
exported the workpaper of the **oldest** run. Twenty-three task-scoped reviews — each with a
single-run fixture — all passed; the bug only appears with two runs, and every order looks identical
with one element. The whole-branch review caught it.

## The rule

- Changing a producer's ordering (a SQL `ORDER BY`, a sort, a reverse) is a cross-cutting change.
  Whenever you flip a query to `DESC`/`ASC` (e.g. newest-first for a UI), grep its consumers for
  **positional** access (`[-1]`, `[0]`, `.first()`, `.last()`) and re-confirm each still means what it
  did. Prefer selecting by an explicit key (`max(..., key=...)`) over positional indexing when
  "latest"/"earliest" semantics matter; if you must index positionally, normalize the order at the
  seam (reverse the DESC list to ASC) and comment why.
- Single-item TDD fixtures cannot catch an ordering bug — add a deliberate **2+ record** fixture for
  any "pick the latest/first" logic, and run a **whole-branch review with multi-item data** as the net
  for seam bugs that per-task, single-item reviews structurally miss.

## Reference

- `store/repo.py` `list_runs_for` (`ORDER BY executed_at DESC`); `store/export_service.py`
  `_to_run_dicts` (reverses DESC→ASC at the seam); `bundle/assemble.py` `_build_workpaper`
  (`runs[-1]` = latest under ASC).
- Regression test: `tests/bundle/test_assemble.py::test_workpaper_reflects_latest_run_not_oldest`.
