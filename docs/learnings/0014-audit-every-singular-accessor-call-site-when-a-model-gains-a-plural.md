---
id: 0014
date: 2026-06-22
area: refactor
tags: [pipeline, model, back-compat, migration, review]
status: active
supersedes: null
superseded_by: null
---

# When a model gains a plural accessor where a singular existed, audit EVERY singular call site — the back-compat alias hides the misses

## Context

The pipeline gained `Pipeline.terminals` (all terminal sinks) to support a control forking into N
terminal Test nodes; `Pipeline.terminal` was kept as `terminals[0]` for back-compat. Per-task TDD and
reviews were green, but one consumer was missed.

- `pipeline/rowcounts.py` still used the singular `.terminal` and skipped only `terminals[0]` in its
  emit loop. On a forked pipeline the other terminal got no output frame → the generated row-count code
  referenced an undefined variable → the probe raised → `_row_counts` returned `{}` → the editor showed
  live row-counts of "—" for **every** node. Preview-only, no crash, but a real regression on the
  feature's own surface. Only the whole-branch review caught it.

## The rule

When a model gains a **plural** accessor where a **singular** one existed and you keep the singular as a
back-compat alias (`singular = plural[0]`), treat that alias as a **migration hazard, not a
convenience**: it silently compiles for every consumer that should now handle ALL elements. Before
merging, **grep every singular-accessor call site** (here `\.terminal\b`) and decide per-site whether it
needs one or all. Per-task TDD and single-element fixtures cannot surface the miss — the singular alias
keeps them green — so add a **multi-element fixture** for each consumer and do a **whole-branch sweep**
of the accessor. Mirror the already-migrated consumer's pattern (here `compile.py`'s multi-terminal
`_emit_python`/`_emit_terminal`) rather than re-deriving it.

## Reference

- `uticen_lite/pipeline/model.py` (`Pipeline.terminals`, `Pipeline.terminal`).
- `uticen_lite/pipeline/rowcounts.py` (the missed site) and `uticen_lite/pipeline/compile.py`
  (the correct multi-terminal pattern to copy); fix commit `1c93f8f`.
- Same family as [[0004]] — a change whose breakage a back-compat / single-record shim hides from local
  tests, caught only by a 2+-element fixture and a whole-branch review.
