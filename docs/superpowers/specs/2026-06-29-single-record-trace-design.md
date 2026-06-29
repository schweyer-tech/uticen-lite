# Single-record trace — design

> Spec for GitHub issue **#29** — "Single-record trace: walk one row through the test → doubles as
> workpaper evidence." Brainstormed 2026-06-29. Status: **approved, ready for an implementation plan.**

## Summary

Let an author **pick one record by its item key and watch it walk through the control's logic**, node
by node, and see exactly where (and why) it is flagged as an exception — or why it passes. The same
object is **two features at once**: a best-in-class debugging tool for a non-developer (the #9
*silently-empty / silently-wrong* pain), and the **audit-narrative sentence** for a flagged record.

This spec covers the **thin first cut**, scoped by four decisions made during brainstorming:

1. **Job: the any-record debugging trace** (pick *any* record, flagged or not), not just a per-flagged
   exception explainer. This is the half that kills the #9 silently-empty pain.
2. **Placement: a dedicated `Logic ▸ Trace` sub-tab.** Not overlaid on the Builder cards or the
   Flowchart SVG.
3. **Depth: full per-node walk + condition detail.** Walk the record through every pipeline node
   (present / dropped / indeterminate), then per-condition pass/fail at each Test node.
4. **Record selection: type/paste the item key**, plus a few example-key chips to seed exploration.
   (Click-to-trace from a population table is a deferred alternative.)

## Why it fits the strategy

- Serves the top roadmap priority **#9 (usable no-code authoring)**: the trace makes a silently-empty
  or silently-wrong result *legible* — "this row dropped out at the Filter / matched no invoice at the
  Join / failed the approver≠creator test."
- On the **"audit-grade evidence"** north-star: the per-condition block for a flagged record *is* the
  workpaper evidence sentence.
- **Render-only**, so it touches neither the bundle contract nor the store schema (learnings
  [0001](../../learnings/0001-stay-compatible-with-the-uticen-app.md),
  [0015](../../learnings/0015-verdicts-and-thresholds-are-render-store-only-never-in-the-bundle.md)).
  Nothing here is persisted or exported.

## Context the design builds on

The codebase is further along than issue #29 assumed (it predates #25 landing):

- **The pipeline graph is the authoring representation.** Even a plain `rule_spec` control derives an
  `Import → Test` builder graph (`derive_builder_graph` / `_pipeline_for_view` in
  `uticen_lite/plane/routes/pipeline.py`). "The conditions" live in the **terminal Test node's**
  config (`config["conditions"]`, `config["logic"]`). So "trace against the rule_spec" means "trace
  against the Test node's conditions."
- **Per-node materialized frames already exist.** `_materialize_full(conn, root, pipeline)` returns
  `{node_id: DataFrame}` — each node's *output* rows over the **full population**, LRU-cached and
  content-addressed (learning
  [0030](../../learnings/0030-incremental-dag-recompute-via-content-addressed-ancestor-closure-keys.md)).
  The step inspector (`GET /controls/{id}/logic/step/{node_id}/data`) already pages through them.
  Checking "is my record still present after the Filter / after the Join?" is therefore a membership
  check on frames we already compute.
- **`_condition_mask(df, cond, sources)`** in `uticen_lite/rules/evaluate.py` is the canonical
  per-condition evaluator (handles `eq`/`ne`/`gt`/…/`in`/`regex`/`is_duplicate`/`exists_in`/
  `not_exists_in`). The trace reuses it verbatim so the per-condition verdict matches the real run.

## Surface & routes

A new **Logic sub-tab "Trace"**, following the server-rendered sub-route pattern (learning
[0007](../../learnings/0007-control-plane-editors-are-server-rendered-sub-route-tabs.md)):

- `GET /controls/{control_id}/logic/trace` — the Trace tab. Optional `?key=<value>` query param.
  - Registered **before** the `/controls/{control_id}` catch-all (learning 0007), in
    `pipeline.register()` alongside the other `/logic/*` sub-routes.
  - Plain GET, no HTMX (matches `step_data` style): a trace is bookmarkable / shareable, and the URL
    carries the key so a refresh re-traces.
- `_logic_tabs.html` gains a `Trace` entry next to Builder / AI / Flowchart / Python.

**Record picker:** a text input ("Trace item key — type or paste a value from the *<key column>*
column") that submits the key as `?key=…`. Below it, **3–5 example-key chips** drawn from the
population's key column (one-click to trace). The text input is the primary path because the #9 pain
is concrete ("I expected `PO-1234` flagged"); the chips seed exploration when the author doesn't know
a key offhand.

## What the trace renders

Given a `key`, over the already-cached `_materialize_full` frames:

1. **Resolve the key column** (the first Import's population key column) and find the matching row in
   the first Import's output frame.
   - **Absent** → *"No record with key `X` in `<source>`."* (Catches typos / wrong key column —
     itself a #9 win.)
   - **Non-unique** (the key matches ≥2 rows) → trace the **first** match and note *"N records share
     this key; tracing the first."*
2. **Walk nodes top-down** (`pipeline.topological()`). Each node gets a status derived from the
   materialized frames:
   - **present** — a row with this key is in the node's output frame.
   - **dropped here** — present at the node's input but absent from its output → this node excluded
     the row. Labelled by node type: *"Filter excluded it"*, *"Join found no match"*, etc.
   - **can't track past here (indeterminate)** — the key column is not present in this node's frame
     (e.g. a Join coalesced/renamed the key — learning
     [0021](../../learnings/0021-detect-join-misses-via-a-brought-marker-not-the-coalesced-key.md)).
     Shown honestly as indeterminate, **never** a false "dropped."
3. **At each Test node the record reaches** — per-condition detail:
   - For each `Condition`, evaluate `_condition_mask` on the **Test's input frame**
     (`frames[test.inputs[0]]`) over the full population, then index this row → show
     **op · column · actual value · ✓/✗**. For `exists_in`/`not_exists_in`, show the looked-up value
     and whether it was found.
   - The combined `all`/`any` verdict = is the row in the **Test's output frame** →
     **"Flagged as an exception"** or **"Passed — not flagged."**

The per-condition block for a flagged row is the audit-narrative sentence; the walk's drop-point is
the silently-empty cure.

## Components

### `trace_record(...)` — pure view-model builder (new)

A pure helper in its own small, pandas-isolated module (sibling to `pipeline/materialize.py`; e.g.
`uticen_lite/pipeline/trace.py`). Signature roughly:

```
trace_record(pipeline: Pipeline, frames: dict[str, DataFrame], key: str,
             sources: dict[str, Population]) -> TraceResult
```

Returns a serializable view-model: the resolved key column + source, a `not_found` / `shared_count`
flag, an ordered list of **node steps** (`{id, label, type, status: present|dropped|indeterminate,
reason}`), and for each reached Test a list of **condition rows**
(`{op, column, value, actual, passed, note}`) plus the Test verdict (`flagged: bool`). Keeping it pure
and pandas-isolated means the route/template stay thin and it is unit-testable without a web client.

### `logic_trace` route (new, in `pipeline.py`)

Loads the control, `_pipeline_for_view(control)`, `_materialize_full(...)`, builds the bound `sources`
map (for `exists_in` conditions — same load path the run uses), calls `trace_record`, renders
`logic_trace.html`. Read-only GET → takes `Depends(get_conn)` (learning
[0002](../../learnings/0002-fastapi-sqlite-per-handler-connection.md)). The **entire body is wrapped
"never raises"** (learning
[0013](../../learnings/0013-derived-editor-state-must-round-trip-and-tolerate-incomplete-graphs.md),
[0033](../../learnings/0033-never-500-is-convert-at-source-plus-backstop-not-an-exception-allowlist.md)):
any failure degrades to a friendly message, never a 500.

### `logic_trace.html` + `Trace` tab in `_logic_tabs.html` (new / edit)

Extends `base.html`, includes the shared logic sub-tab nav, renders the picker + the trace
view-model. Reuses existing design tokens / table styles (learning
[0005](../../learnings/0005-control-plane-reuses-workpaper-design-tokens.md)).

### Reuse (no new logic)

`_pipeline_for_view`, `_materialize_full`, `_node_label`, `_condition_mask`, `pd_isna`,
`is_raw_python`, the bound-sources load path, the sub-tab nav, base layout.

## Correctness notes

- **Masks evaluated on the full population, then indexed to the one row** — `is_duplicate` and
  `exists_in`/`not_exists_in` are population-relative, not row-local; filtering to one row first would
  silently break them. This mirrors the step inspector's "run over the full population" decision.
- **Key column identity across nodes.** Track membership by the Import key column where it is present
  in a node's frame; where it is absent (post-Join coalesce/rename), mark **indeterminate** rather
  than guessing. The Test node's input frame retains the key in the single-source case, which is the
  cut that must work cleanly.
- **Multiple Test terminals (procedures).** A control may have several Test terminals; show condition
  detail for **each** Test the record reaches.

## Graceful degradation & never-500

Each of these renders a friendly message, asserted by a test; the route never 500s:

- **Raw-Python control** (`is_raw_python`) → *"Tracing needs the rule builder; this control is
  authored in Python."*
- **Unbound source / no materialized frames** → *"Bind a data source to trace a record."*
- **Key not found** → *"No record with key `X` in `<source>`."*
- **Key column unresolvable** (incomplete graph) → *"This control isn't ready to trace yet."*
- **Condition eval error** (unknown `other_source`, dtype mismatch raising in pandas) → a friendly
  per-condition note, not a crash. Reuses the never-500 discipline from learning 0033 (convert at
  source + backstop, not an exception allowlist).

## Boundaries (non-goals for this cut)

- **No persistence, no bundle, no `schema_version` change.** Render-only; nothing is written to the
  store or the export bundle.
- **Not on the Run view or workpaper** this cut (the run-view/workpaper "explain each flagged
  exception" surface — Job A — is a separable follow-up; see issue #29 and #25 §5).
- **Not the visual flowchart overlay.** A dedicated sub-tab, not annotations on the read-only SVG.
- **rule_spec / builder controls only.** Python escape-hatch controls degrade gracefully (above).

## Testing strategy

- **Unit — `trace_record` over Northwind fixtures** (`examples/northwind-trading`):
  - a **flagged** key (`vendor-master-sod`, approver = creator) → the matching condition shows ✓ and
    the Test verdict is **Flagged**;
  - a **passing** key → conditions ✓-where-expected and verdict **Passed — not flagged**;
  - a key **dropped by a Filter / Join** → the walk shows the drop at that node;
  - a **missing** key → `not_found`;
  - a **non-unique** key → first-match traced + `shared_count`;
  - a **raw-Python** control → degraded (no condition detail).
- **e2e browser** (learning
  [0012](../../learnings/0012-rerun-e2e-browser-smoke-on-htmx-swap-changes.md) — this adds a Logic
  surface): open the Trace tab, type a key, assert the verdict + condition rows render; click an
  example chip and assert it traces.
- **Never-500** (learning 0033 family): trace against a control whose source was deleted, and a
  control with a bad-regex condition → asserted non-500 + friendly copy.
- Keep the suite **pristine** (no stray warnings), `ruff` + `mypy uticen_lite` green.

## Out-of-scope follow-ups (not this spec)

- Job A: an "explain this flagged exception" expander on the Run view / workpaper (the evidence half),
  reusing `trace_record`'s condition block via a shared partial.
- Click-to-trace from a population preview table.
- A flowchart overlay that highlights the traced record's surviving path.
