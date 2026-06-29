---
id: 0021
date: 2026-06-22
area: pipeline
tags: [pipeline, join, custom-python, pandas, merge, authoring]
status: active
supersedes: null
superseded_by: null
---

# Detect a Join "miss" via a brought non-key marker column, not the coalesced join key — and reach the other source's columns only through the Join

## Context

Reshaping the Northwind `three-way-match` control from a standalone `test.py` into a visual
pipeline (Import payments → LEFT Join invoices → LEFT Join purchase_orders → one Custom Python
node) needed the node to tell apart *invoice missing* from *PO missing*. The intuitive marker —
the right-side join key surviving as `<key>_joined` — does not exist: the compiled merge
(`uticen_lite/pipeline/compile.py::_emit_join`) uses `left_on==right_on` with
`suffixes=('', '_joined')`, and pandas **coalesces a same-named join key into a single column**
(no `invoice_id_joined`/`po_id_joined`). The `_joined` suffix only lands on **colliding non-key**
columns — e.g. the PO `amount` collides with the payment `amount` and becomes `amount_joined`.

So after a LEFT join the join-key column is non-null for matched *and* unmatched rows alike, and
can't signal a miss. A starved Custom Python node also cannot read `sources` (lexical starvation,
[[0008]]), so the only way it sees another source's columns at all is via what the Join brings.

## What went wrong / what worked

Relying on a `<key>_joined` column silently mis-classifies every row (the column isn't there).
The fix: in the Join's `bring_columns`, include a column that is **always populated when the row
matches** (here `invoice_date`) and test *that* for null in the node (`x is None or x != x`) to
detect a miss. For colliding non-key columns, address them by their `_joined` name (`amount_joined`
= the PO amount). Verified the reshaped pipeline reproduces the exact baseline item_keys
(PMT-C3A/B/C/D) by importing + running the example and diffing against the pre-change run — the
[[0009]] equivalence discipline.

## The rule

When a Custom Python node sits behind a Join: (1) it reaches the other source **only** through the
Join's output — pull every needed column in via `bring_columns`, never `sources[...]`; (2) to
detect an unmatched (LEFT-join) row, test a **brought non-key column** that is always present on a
match, **not** the join key (the merge coalesces the same-named key into one always-present column,
so it can't mark a miss) and **not** a `<key>_joined` column (it isn't emitted); (3) a colliding
non-key column from the right arrives suffixed `_joined` (`suffixes=('', '_joined')`) — reference it
by that name. Prove equivalence by diffing violation item_keys against the pre-change run ([[0009]]),
not just the count. Keep the node within the allowlist ([[0008]]): no `import pandas` (operate on the
`rows` frame's methods + `re`/`datetime`/`decimal`), no file reads, no dunders.

## Reference

- `uticen_lite/pipeline/compile.py::_emit_join` (the `left_on/right_on` + `suffixes=('', '_joined')` merge).
- `uticen_lite/pipeline/lint.py` (custom-node allowlist; [[0008]]).
- `examples/northwind-trading/controls/three-way-match/pipeline.yaml` (brings `invoice_date` as the match marker; uses `amount_joined`).
- Equivalence discipline: [[0009]]; lexical starvation: [[0008]].
