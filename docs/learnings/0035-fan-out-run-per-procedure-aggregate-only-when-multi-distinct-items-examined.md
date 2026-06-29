---
id: 0035
date: 2026-06-28
area: backend
tags: [control-plane, run, procedures, rollup, population, store]
status: active
supersedes: null
superseded_by: null
---

# Fan out a control's run per procedure, but persist the `procedure_id=''` aggregate ONLY when there are ≥2 procedures; count population as distinct items examined from each test's INPUT frame, and dedupe the aggregate by item-key

## Context

Procedures became a node-grouping layer: a control's run fans out into one `RunRecord` per
effective procedure (each check runs as its own single-terminal sub-pipeline; violations merge;
the control-level `procedure_id=''` aggregate is for the run-view lookup). The original
multi-terminal path persisted the aggregate only for forked controls.

## What went wrong

- The aggregate-persistence guard was written as `if len(per_proc_runs) != 1 or agg_population is not None:` — which is **always** true on a real run (frames materialize), so **every single-procedure pipeline control double-persisted** a redundant identical `procedure_id=''` aggregate (1→2 runs). Run history + the exported bundle inflated; the Northwind demo went 9→16 runs.
- A first draft also tried to read population from each test's materialized OUTPUT — but `materialize_steps` returns a terminal Test node's **violating** rows, not the rows it examined.
- Unit tests on a single control shape stayed green; the regression only surfaced in a multi-control demo count assertion and the dashboard "Failed" tile.

## The rule

- When a control's run fans out into per-procedure `RunRecord`s, persist a separate
  control-level `procedure_id=''` aggregate **only when `len(per_proc_runs) > 1`**. A
  single-procedure control persists **exactly one** run (its per-procedure run, tagged with
  the procedure id). Never gate the aggregate on "frames available" / "always" — that
  double-persists every single-procedure control and inflates run history + the bundle.
- Report a procedure's population as **distinct items examined** = the union of distinct
  item-keys across each constituent test's **INPUT** frame (`node_frames[test.inputs[0]]`),
  NOT the test's materialized output (for a terminal that is the *violating* rows). Degrade
  to the run's population (never raise) when frames are unavailable ([[0013]]).
- The control-level aggregate **dedupes violations by item-key** (an item flagged by ≥2
  checks is one exception, annotated via `details["checks"]` with the checks that fired);
  keep `details` JSON-native by funnelling through `Violation.from_raw` ([[0020]]).
- Prove the run-count **and** population invariant with a test per control **shape**
  (single-procedure vs multi-procedure) — a unit test on one shape stays green while the
  single-vs-multi invariant breaks (same per-shape-audit trap as [[0014]]).

## Reference

- `uticen_lite/store/run_service.py` — `_run_multi_procedure` (the `if len(per_proc_runs) > 1:` aggregate guard), `_distinct_examined`, `_merge_violations`, `_severity_rank`.
- `tests/store/test_run_service_procedures.py` — the per-shape run-count + distinct-examined + which-checks assertions.
- Procedures live store-only in the `pipeline` JSON and compile to the existing artifact ([[0010]]); verdict/threshold stay render/store-only ([[0015]]).
