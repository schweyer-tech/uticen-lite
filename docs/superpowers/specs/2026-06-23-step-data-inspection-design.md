# Step-data inspection & per-step workbook export — design

> Spec date: 2026-06-23. Status: approved for planning.
> Surfaces: control-plane (`controlplane`) pipeline builder + a new local Excel export.

## Problem

When authoring a control pipeline, the plane shows a **row count** on each step ("rows: 1,234")
but the data behind that number is invisible. Authors can't see *what* rows survive a filter, *what*
a join produced, or *how* the population shrinks step by step on the way to the violations. They want
to:

1. **Click a step's row count and see the actual rows** at that step (and optionally export that one
   step to Excel).
2. **Export the whole logic flow as a workbook** — one sheet per step showing what the data looked
   like at each step — so they can inspect, in Excel, how the data changed as it flowed through the
   pipeline.
3. **Build iteratively**: add or edit a step and have results recompute *from that step onward*,
   reusing unchanged upstream work, so the edit→see-the-effect loop is fast.

This is the **"view" stage** of the control plane (author → run → view → export). It makes a
full-population test *inspectable* so an analyst can trust it.

## Decisions (locked during brainstorming)

| Question | Decision |
| --- | --- |
| What does "over time / each step" mean? | **Pipeline-step progression** (data as it flows import → filter → join → test), **not** cross-run history. |
| Export depth | **Full population** per step (with an Excel row-limit guard + truncation note). |
| Export format | **Excel `.xlsx`, one sheet per step**, plus a summary/index sheet. |
| How is step data computed? | **Approach A — on-demand materialization engine** in the pandas layer; nothing persisted to store/bundle. |
| Live builder badges | **(ii) full-population everywhere** — made viable by the incremental cache. |

## Non-goals (strategy guardrails)

- **Not in the bundle.** These are localhost-only evidence/inspection artifacts. Raw population rows
  never enter `contract/bundle.schema.json`, the store, or any persisted run. Cardinal rule (learning
  0001) is untouched; `schema_version` is not bumped; no store migration.
- **Not a general analytics tool.** The inspector is *pagination only* (plus the cheap column sort the
  existing preview already has). No ad-hoc querying, filtering expressions, pivots, or charts.
- **Not cross-run history.** Comparing a control's data across runs on different dates is a separate
  feature (issue #14) and out of scope here.

## Architecture

### The materialization engine (new)

New module **`uticen_lite/pipeline/materialize.py`** — pandas/CPython layer, alongside
`rowcounts.py` (so it respects the Pyodide boundary: the Pyodide-safe core — `model/`, `compile.py`,
`runner/execute.py` — stays pandas-free).

It generalizes what `pipeline/rowcounts.py` already does. Today that probe compiles the pipeline,
execs it over real pandas frames, and keeps only `len(_f_<id>)` after each node. The new engine keeps
the **frames**:

```python
def materialize_steps(pipeline, frames, scope) -> dict[str, "DataFrame"]:
    """Return {node_id: DataFrame} — the data at every step, over the given scope."""
```

- Reuses `compile.py`'s per-node line emission (`_emit_node_lines`) so the data at each step is
  byte-for-byte the same computation the real run performs (no second interpreter — learning 0009
  spirit).
- Reuses the existing custom-Python sandbox: the same module-level `_node_<id>` helpers the runner
  compiles, run behind the 3-layer guard (learning 0008). **No new security surface.**
- `scope` selects the population: `full` (real full population) or `sample(N)`. Per the decision,
  every plane surface uses `full`; `sample` remains available for tests/perf fallback.

**DRY refactor:** `compute_row_counts` collapses to
`{nid: len(df) for nid, df in materialize_steps(...).items()}`. The badge count and the data you open
become the *same* computation — they can never disagree. `rowcounts.py`'s public surface
(`compute_row_counts`, its graceful `{}`-on-missing-source behavior) is preserved.

### Incremental recompute ("recalculate from that step onward")

A content-addressed per-step cache:

```
_STEP_CACHE: dict[step_key, DataFrame]
```

`step_key(node)` = a stable hash over:

- the **ancestor-closure** of the node — its full upstream subgraph in topological order (the pipeline
  is a DAG; a join has two inputs, so this is the DAG ancestors, not a flat 1..K prefix), serialized
  via the node definitions; **plus**
- a **version token** for every source imported within that closure (source id + extract-date /
  content hash), so a changed source busts the key.

Materialization walks the DAG in topological order:

- **Cache hit** → seed the cached frame directly into the exec namespace as `_f_<id>` and emit **no**
  compute lines for that node.
- **Cache miss** (the edited/added node and everything downstream of it) → emit and exec only those
  node blocks, seeded by their now-cached upstream inputs.

So editing or adding a step recomputes exactly that step forward; unchanged upstream frames are
reused. Invalidation is automatic — a changed step produces a new key and stale entries age out under
an **LRU bound (capped by frame count)** to cap memory. Nothing is persisted: in-memory, per-process,
single-user. (Minor: guard the dict with a lock or rely on CPython atomicity for the threadpool;
single-user makes contention negligible.)

## Surfaces & UX

All three share the engine. All localhost-only; none touch the bundle.

### 1. Clickable counts → step inspector

The row-count badge becomes the click target in both places it appears today:
`templates/partials/_pipe_node.html` (builder node cards) and
`templates/partials/_pipe_diagram.html` (diagram boxes).

Clicking opens an **HTMX-loaded inline panel/drawer in the builder** (not a separate page) showing
that step's **full-population** frame, **server-side paginated** (~100 rows/page, "records X–Y of Z"),
reusing the `source_data.html` paginated-preview pattern (the workpaper's load-everything 500-row
table won't scale to full population). The panel header names the step ("Step 2 · filter") and carries
the two export buttons. Clicking a different node swaps the panel to that node.

Inline-drawer (vs. a sub-route page) is deliberate for the iterative loop: as a step is added/edited,
the badges recompute incrementally and the open panel refreshes to the new data at that step —
edit → see-the-effect, in place.

### 2. Per-step export

A **"Download this step (.xlsx)"** button in the inspector header → a single-sheet workbook of that
step's full-population frame.

### 3. Full step workbook

A **"Export step workbook (.xlsx)"** button at the pipeline level (alongside the existing run/export
controls). One sheet per step in flow (topological) order, plus a **summary/index sheet** listing:
step number, node type, full label, row count, the control's metadata, and a generation timestamp.
Sheet names sanitized to Excel rules (≤31 chars, no `[] : * ? / \`, unique) as `1 - import`,
`2 - filter`, …; the summary sheet holds the full labels.

### Routes (following `routes/pipeline.py` conventions)

| Route | Returns |
| --- | --- |
| `GET …/pipeline/step/{node_id}/data?page=N` | inspector partial (paginated table) |
| `GET …/pipeline/step/{node_id}/export.xlsx` | single-sheet `FileResponse` |
| `GET …/pipeline/export-steps.xlsx` | multi-sheet `FileResponse` |

Both `.xlsx` writers live in the pandas layer and are `[adapters]`-gated (see below).

## Edge cases & boundaries

- **`[adapters]` gating (one gate for the whole feature).** Materialization needs pandas; the xlsx
  writer needs openpyxl — both ship together in `[adapters]`, already required for the row-count
  badges to compute. Nothing regresses. If `[adapters]` is absent, export buttons render disabled with
  a "requires the `[adapters]` extra" hint, and routes return a friendly `AdaptersUnavailable` message
  (catch `ImportError` **before** any broad catch — learning 0024) — never a 500.
- **Excel limits.** A sheet caps at 1,048,575 data rows; if a step's population exceeds it, the sheet
  is truncated to the limit with a **clear truncation note** in the summary sheet (and a banner row).
  The inspector is server-paginated, so it has no limit. Column limit 16,384 — guarded, unrealistic
  for control data.
- **Cell coercion for Excel** (one shared helper, learning-0020 philosophy): keep native types Excel
  handles well (numbers, dates); coerce the rest so openpyxl can't choke — `Timestamp`→datetime,
  `NaT`/`NaN`→empty, numpy scalar→`.item()`, lists/dicts (from custom-python nodes)→`str`.
- **Not-yet-computable steps.** Missing/unbound source, incomplete graph, or a custom-Python node that
  raises: the engine returns partial results (existing graceful `{}` behavior); the inspector panel
  shows a friendly "this step isn't computable yet — <reason>"; the builder never 500s (learning
  0013). Downstream-of-error steps are marked uncomputable, not crashed.
- **Cache discipline.** In-memory, per-process, single-user; LRU-bounded by frame count. Keys are
  content-addressed (ancestor-closure + source-version), so changes bust keys and stale frames age
  out — no manual invalidation, nothing on disk or in the store.
- **Trust boundary (cardinal rule, learning 0001).** Local evidence artifacts only. `.xlsx` files are
  written to a tempfile and streamed via `FileResponse`; raw rows never enter the store or the bundle.
  The author-code `to_excel` deny-list is unrelated (it gates *untrusted* node code; our writer is
  trusted SDK code).

## Testing

- **Equivalence / DRY (learning 0009 spirit):** after refactoring `rowcounts` onto the engine, assert
  `len(materialize_steps()[nid])` equals the prior per-node counts and the terminal step equals the
  real run's violation count, on real Northwind fixtures incl. join + custom-python.
- **Incremental cache:** with a recompute spy, assert editing a *downstream* step reuses upstream
  frames (upstream compute count = 0) and editing an *upstream* step recomputes it + all descendants
  (new keys); assert a changed source-version busts the key.
- **xlsx writers:** one sheet per step + summary sheet; sheet-name sanitize/dedup/truncate; sheet
  content matches the full-population frame; row-limit truncation emits the note; cell-coercion cases
  (`Timestamp`/`NaT`/numpy/list) write without error and round-trip sensibly.
- **`[adapters]`-absent path:** routes degrade friendly; buttons gated (assert the friendly message,
  not a 500).
- **Inspector route:** pagination math ("records X–Y of Z"); missing-source and incomplete-graph both
  degrade without 500.
- **Trust-boundary teeth-check (learnings 0001/0026):** a positive test that after these surfaces
  exist, a built bundle still contains **zero** raw rows.
- **e2e browser smoke (learning 0012):** since this restructures builder HTMX, run/update
  `tests/e2e -m browser` — click a count → drawer opens → paginate → export downloads.

## Files (anticipated)

- **New:** `uticen_lite/pipeline/materialize.py` (engine + cache); an xlsx-writer helper (in the
  pandas layer — e.g. `uticen_lite/adapters/xlsx_export.py` or a `pipeline/` sibling); inspector
  partial template; per-step + workbook export wiring.
- **Modified:** `uticen_lite/pipeline/rowcounts.py` (consume the engine); `routes/pipeline.py`
  (3 new routes); `templates/partials/_pipe_node.html` + `_pipe_diagram.html` (clickable counts);
  builder template (drawer + export buttons); `pyproject.toml` only if a new optional dep is needed
  (none anticipated — openpyxl already in `[adapters]`).
- **Untouched (asserted by tests):** `contract/bundle.schema.json`, `bundle/`, store schema/migrations.

## Out of scope / future

- Cross-run history & diffing (issue #14).
- Disk-backed cache surviving restarts (reintroduces raw-data-at-rest; revisit only if memory pressure
  is real).
- Inspector querying/filtering beyond pagination + column sort.
