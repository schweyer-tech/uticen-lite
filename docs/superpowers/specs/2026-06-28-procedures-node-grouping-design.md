# Design — Procedures as a node-grouping layer on the Logic page

> Status: **approved design, pre-plan.** Date: 2026-06-28. Author surface: `controlplane` web app.
> Cardinal rule in play: this touches the bundle (`procedure` object) — additive-optional only,
> no `schema_version` bump. See [learning 0001](../../learnings/0001-stay-compatible-with-the-uticen-app.md).

## Problem

A consultant authoring a control needs to describe it the way an audit program does: a control
(e.g. `Finance.GL.1`) is performed via one or more named **procedures** — `P1: Manual Journal Entry
Review (Segregation of Duties)`, `P2: Late Posting Review (Cutoff)` — and the workpaper documents each
procedure's population, exceptions, and conclusion separately.

Today the SDK has only an **implicit** notion of a procedure: a "procedure" = a terminal `Test` node
in the Logic graph, and a fork into ≥2 terminals yields N procedures whose membership (which upstream
steps belong to each) is inferred purely from topology (`_subpipeline_for` = the terminal's ancestor
closure). There is no first-class procedure identity (no stable code, name, or assertion), no
author-facing way to **define** procedures and **wire** workflow nodes to them, and a procedure cannot
roll up more than one test.

## Goal

Let the author **define procedures** (code · name · assertion · threshold) and **assign workflow
nodes to them**, where a procedure may roll up **several** `Test` nodes into one audit result. Keep the
Uticen-app bundle contract intact (additive-optional only). Reuse the existing graph / compile /
run / workpaper machinery rather than forking it.

### Non-goals

- Not a cross-control / cross-engagement **procedure library** (no reuse across controls). Single
  control, single engagement — consistent with `STRATEGY.md`.
- Not a controlled-vocabulary assertion taxonomy. Assertion is **free text** (suggestions may come
  later).
- Not documentation-only procedures (no manual/no-test procedures) — every procedure resolves to ≥1
  `Test` node and produces a real result. (The "control-level checklist" interpretation was
  explicitly rejected during brainstorming.)
- No change to the CCM loop, multi-tenancy, or live connectors (out of SDK scope).

## Decisions captured during brainstorming

| # | Decision | Choice |
| - | -------- | ------ |
| 1 | What a "procedure" is relative to the graph | **Grouping layer over nodes** (not just a renamed terminal) |
| 2 | Procedure ↔ Test cardinality | **Multiple Tests, rolled up** into one procedure result |
| 3 | Rollup math when tests filter differently | **Distinct items examined** (union of evaluated rows by item-key) |
| 4 | Authoring surface | **Side panel + per-Test "Procedure ▾" selector**, support-node membership **derived**; **procedure colors** carried into Builder cards, Flowchart SVG, and workpaper |
| 5 | Assertion/category field | **Free text** (optional `assertion` line per procedure) |
| A | Population basis scope | **Uniform** — every procedure (single- or multi-test) reports distinct-items-examined; accept demo/fixture + count-assertion fan-out (learning 0031) |
| B | `code`/`assertion` in the bundle | **Additive-optional** on the bundle workpaper procedure object; no `schema_version` bump |

## Core model

### Procedure (first-class, store-only)

A procedure definition lives **inside the existing `controls.pipeline` JSON blob** (already a
store-only TEXT column) as a sibling array to `nodes`:

```jsonc
{
  "nodes": [ /* unchanged */ ],
  "procedures": [
    {
      "id": "p_8f3a",                 // stable internal id
      "code": "P1",                   // author-visible label; default auto from position, editable
      "name": "Manual Journal Entry Review",
      "assertion": "Segregation of Duties",   // free text, optional
      "narrative": "We examined all manually-posted journal entries …",
      "failure_threshold_pct": null,  // per-procedure threshold (one of pct/count or neither)
      "failure_threshold_count": 0,
      "position": 0                   // ordering + color index
    }
  ]
}
```

- **No new table, no store-schema migration.** The `pipeline` column already exists; we enrich its
  JSON. Old blobs simply lack `procedures` (handled by the default in §Back-compat). This stays
  consistent with [learning 0010](../../learnings/0010-new-authoring-representation-compiles-to-the-existing-artifact.md)
  (new authoring state is store-only and compiles to the existing artifact) and avoids the
  store-`SCHEMA_VERSION` bump rule ([0022](../../learnings/0022-bump-the-store-schema-version-constant-when-adding-a-migration-step.md))
  because there is no new migration step.
- The `Pipeline` dataclass (`pipeline/model.py`) gains `procedures: list[ProcedureDef] = []`
  (frozen). `parse_pipeline` parses/validates it; serialization round-trips it.

### Node → procedure wiring

- **Test nodes** carry `config["procedure_id"]` = the owning procedure's `id`. A Test belongs to
  **exactly one** procedure (so its exceptions aren't double-counted).
- **Support nodes** (Import/Filter/Join, and `custom_python` flavor=transform) carry **no** stored
  procedure tag. Their membership is **derived**: a support node belongs to the union of the
  procedures of the Test nodes in its downstream closure. This is computed for display
  (color chips, swimlane hints) and never persisted — there is no invalid state to maintain.
- **Colors** are derived from `position` (a fixed palette), never stored.

### Rollup semantics (the load-bearing part)

For a procedure `P` owning tests `{t1…tk}`:

- **Evaluated set of a test** `ti` = the rows of its **input** frame (`ti.inputs[0]`'s materialized
  output) — the post-filter population the test actually ran over. Its item-key column is
  `ti.config["item_key_column"]` (fallback: the frame index, matching today's `str(idx)` behavior).
- **Population (denominator)** = `|⋃ᵢ distinct item-keys of ti's evaluated set|` — *distinct items the
  procedure examined*. (Uniform, per Decision A — applies to single-test procedures too.)
- **Failed (numerator)** = `|distinct item-keys flagged by ≥1 test|` — violations are **deduped by
  item-key**; a single item flagged by two checks is **one** exception, annotated with which checks
  fired.
- **Pass rate** = `1 − failed/population`. **Verdict** = evaluated against `P`'s threshold
  (`pct`/`count`), else the control threshold.
- **Overall control verdict** = unchanged **any-procedure-fails ⇒ control fails** roll-up.

Worked example (the mocked `Finance.GL.1` workpaper):

```
P1 Manual Journal Entry Review · Segregation of Duties
  check ① preparer = approver        flags 8 items   (evaluated 500 manual JEs)
  check ② no approval evidence       flags 5 items   (evaluated 500 manual JEs)
  1 item flagged by both             ⇒ 12 distinct exceptions
  population = 650 distinct manual JEs examined (union of the two checks' evaluated rows)
  verdict vs threshold (≤ 0): 12 > 0 ⇒ FAIL
```

#### Edge cases (must degrade, never 500 — [learning 0013](../../learnings/0013-derived-editor-state-must-round-trip-and-tolerate-incomplete-graphs.md))

- **Unassigned Test** (procedures defined but a Test has no `procedure_id`): at compile/run it falls
  back to an **auto single-test procedure** (its own), so runs never break; the Builder shows a gentle
  "assign this test" nudge.
- **Missing/mismatched item-key columns** across a procedure's tests: distinctness falls back to
  per-test row-index counting (union only well-defined when disjoint). The Builder warns that tests
  rolled into one procedure **should share an item-key column**; runs still complete.
- **Incomplete graph** (a half-authored procedure): live row-count / inspector probes already degrade
  to "—"; the run page renders a friendly "not ready" rather than raising.

## Compile / run changes

- `compile_pipeline_procedures(pipeline)` (in `pipeline/compile.py`) keys off **`pipeline.procedures`**
  instead of one-per-terminal:
  - For each defined procedure, gather its Test nodes, slice the **union of their ancestor closures**
    (`_subpipeline_for` generalized to a set of terminals), and reuse the **existing multi-terminal
    `_out_<id>` union emit** in `_emit_python` to produce one `test(pop, sources)` returning the
    concatenation of that procedure's checks' violations.
  - Fallback when `pipeline.procedures` is empty → **today's one-procedure-per-terminal** behavior,
    byte-identical.
- **Population computation** moves into the pipeline run path (`store/run_service.py`). It materializes
  node frames once via the existing cached `materialize_steps` engine (the same one powering the step
  inspector) and computes each procedure's distinct-examined population from its tests' **input**
  frames. This is set onto the `RunRecord.population_size` before persist. Rule/Python (non-pipeline)
  controls keep `primary.size` unchanged.
- **Procedure RunRecord synthesis**: union the procedure's tests' violations, **dedupe by item-key**,
  merge descriptions/details and record which checks fired (kept JSON-native at `Violation.from_raw`
  per [learning 0020](../../learnings/0020-keep-violation-details-json-native-sanitize-at-from-raw.md));
  severity = max across the merged checks. `failed = len(deduped)`, `passed = population − failed`.
- Per-procedure run rows keep using `runs.procedure_id` (already exists, store-only). The
  `_procedure_run_id` collision-avoidance hash is unchanged.

## Workpaper + bundle

- `model/workpaper.py`: `ProcedureSpec` and `Procedure` gain `code: str` and `assertion: str`
  (both default `""`). `Procedure.to_dict()` emits them (additive). Threshold/verdict/color stay
  **out** of `to_dict()` ([learning 0015](../../learnings/0015-verdicts-and-thresholds-are-render-store-only-never-in-the-bundle.md)).
- `render/html.py` + `render/markdown.py`: the Procedures section renders
  `code · name`, the `assertion` subtitle, the per-check breakdown, the deduped union exceptions table
  with a **"failed check(s)"** column, and the per-procedure verdict pill (N>1 path; N≤1 byte-identical).
  Procedure colors come from `position`.
- `contract/bundle.schema.json` (and the SDK copy under `schema/`): add `code` and `assertion` as
  **optional** properties on `$defs/procedure`; **`required` list unchanged**, `additionalProperties`
  already `true` → non-breaking, **no `schema_version` bump**. `bundle/assemble.py` threads
  `code`/`assertion` through `procedure_info_by_control`.
- Gate tests (`tests/test_contract_export.py`, `tests/schema/test_bundle_schema.py`) updated to assert
  the new optional fields validate and that a produced bundle round-trips.

## Authoring UX (Builder)

- A **Procedures panel** at the top of the Logic ▸ Builder: list/add/reorder procedures, each editing
  `code` (auto-filled, editable) · `name` · `assertion` (free text) · threshold. Server-rendered HTMX
  ([learning 0007](../../learnings/0007-control-plane-editors-are-server-rendered-sub-route-tabs.md)).
- Each **Test card** gains a **"Procedure ▾"** `<select>` bound to the defined procedures (plus an
  inline "＋ new procedure"). Support cards show derived color chips (read-only).
- The save path submits the **derived graph + procedures** (round-trips per
  [learning 0013](../../learnings/0013-derived-editor-state-must-round-trip-and-tolerate-incomplete-graphs.md));
  re-run the e2e browser smoke for the swapped form
  ([learning 0012](../../learnings/0012-rerun-e2e-browser-smoke-on-htmx-swap-changes.md)).
- The Flowchart SVG inherits per-procedure colors + a legend.

## Back-compat / migration

- **No data migration.** A control whose `pipeline` JSON lacks `procedures` derives the default
  **one-procedure-per-terminal** at read time. Existing controls and the Northwind demo keep their
  exact *structure/wiring* until an author defines procedures.
- The N≤1 **rendering shape** is preserved (no per-procedure pills, the existing `assemble` vs
  `assemble_procedures` split and N≤1 guards). However, per **Decision A (uniform)** the reported
  *population value* shifts to distinct-items-examined: an **unfiltered** single-test control is
  byte-identical (evaluated set = trunk), while a **filtered** single-test control's population (and
  pass-rate) changes from pre-filter to post-filter. That value shift is the fan-out owned below — it
  is intentional, not a regression.
- **Granularity of "population":** each procedure's `result.population_size` is *its own*
  distinct-examined count; the control-level "records tested" shown in the workpaper remains the
  existing **sum across procedures** (`Workpaper.records_tested`), unchanged in formula.

## The uniform-population fan-out (Decision A cost — own it explicitly)

Switching every pipeline control's population to distinct-items-examined **changes reported
populations/pass-rates for existing filtered controls**, including the all-no-code Northwind demo.
Per [learning 0031](../../learnings/0031-changing-the-northwind-demo-cardinality-fans-out-to-many-count-assertions.md),
this fans out:

- `grep -rn` the whole `tests/` tree for the affected literals (`== N`, `(N, N)`, `"N rows"`,
  population/pass-rate numbers) across `tests/test_northwind`, `test_import_service`, the contract
  export test, the wheel/build tests, and any render snapshot fixtures — update **every** one.
- Update `examples/northwind-trading` expected outputs, the README, and `PRODUCT-MAP.md` prose.
- Keep the public-API-sourced demo source frozen so CI stays offline + deterministic
  ([0025](../../learnings/0025-one-time-snapshot-to-file-honors-the-no-live-connectors-non-goal.md)).

## Testing strategy

- **Equivalence** ([learning 0009](../../learnings/0009-prove-generated-code-equals-the-interpreter.md)):
  the per-procedure compiled union `test()` must equal the interpreter over fixtures including a
  multi-check procedure and cross-source conditions.
- **Distinct-items math**: a fixture where two checks overlap on one item asserts population =
  union-of-evaluated and failed = distinct-flagged (the 8 + 5 − 1 = 12 case).
- **Dedup/merge**: an item flagged by two checks yields one exception annotated with both checks;
  details stay JSON-native.
- **Back-compat**: a no-`procedures` pipeline derives the same one-per-terminal procedures and the
  same rendering shape; an **unfiltered** single-test control is byte-identical, a **filtered** one
  shows the new post-filter population (asserted explicitly so the shift is intentional, not silent).
- **Contract**: bundle with `code`/`assertion` validates; `required` unchanged; teeth-test that no raw
  population leaks ([0029](../../learnings/0029-trust-boundary-teeth-check-uses-an-include-false-sentinel-column.md)).
- **e2e browser smoke** re-run for the Builder panel + Test-card select swap.
- **Graceful degradation**: unassigned test, missing item-key, incomplete graph → no 500
  ([0013](../../learnings/0013-derived-editor-state-must-round-trip-and-tolerate-incomplete-graphs.md),
  [0033](../../learnings/0033-never-500-is-convert-at-source-plus-backstop-not-an-exception-allowlist.md)).
- Keep ruff/mypy clean and the suite pristine.

## Rough build sequence

1. **Model**: `ProcedureDef` + `Pipeline.procedures`; parse/validate/round-trip; derived-membership
   helper; default-one-per-terminal derivation. (TDD)
2. **Compile**: generalize `_subpipeline_for` to a terminal-set; `compile_pipeline_procedures` keyed
   off `pipeline.procedures`; equivalence tests.
3. **Run**: distinct-examined population via `materialize_steps`; procedure RunRecord synthesis
   (dedupe/merge); uniform population for all pipeline controls.
4. **Workpaper + bundle**: `code`/`assertion` on `Procedure`/`ProcedureSpec`; renderers; schema +
   `assemble.py`; contract gate updates.
5. **Builder UX**: Procedures panel + per-Test selector + derived chips; Flowchart colors; e2e smoke.
6. **Fan-out**: Northwind fixtures, count assertions, README, `PRODUCT-MAP.md`.
7. **(Optional follow-up)** Convert one Northwind control into a multi-procedure showcase for the
   authoring-ladder story.

## Open questions

None blocking. Deferred niceties: assertion typeahead suggestions; a multi-procedure Northwind
showcase (step 7); reusable procedure templates (explicit non-goal for now).
