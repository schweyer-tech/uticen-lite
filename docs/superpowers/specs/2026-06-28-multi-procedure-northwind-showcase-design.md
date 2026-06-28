# Design — Multi-procedure showcase: convert Finance.GL.1 into two procedures

> Status: **approved design, pre-plan.** Date: 2026-06-28. Surface: the `examples/northwind-trading`
> demo engagement. Realizes the deferred "multi-procedure Northwind showcase" follow-up from the
> procedures cycle. **Bundle-additive only** (the `workpaper.procedures` array is already unbounded) —
> no `schema_version` bump (cardinal rule, [learning 0001](../../learnings/0001-stay-compatible-with-the-controlflow-app.md)).

## Problem

Every one of the 9 `northwind-trading` demo controls is **single-procedure** — there is no `procedures:`
array anywhere in the demo, so nothing exercises (or showcases) the multi-procedure rollup, the
collapsible procedure sections, or the per-procedure workpaper. PRODUCT-MAP flags this as a deferred
follow-up.

## Goal

Restructure `examples/northwind-trading/controls/manual-je-review` (**Finance.GL.1**) from a single
Custom-Python procedure into a **two-procedure control** over the **same demo data (unchanged)**. It
becomes the demo's showcase for procedures + the collapsible sections, and mixes the authoring ladder:
one **no-code** procedure and one **Custom-Python** procedure.

### Non-goals

- **No data changes** — the existing `journal_entries.csv` already splits cleanly (below).
- **No new control or source** — the demo control count stays **9** (avoids the control-count fan-out of
  [learning 0031](../../learnings/0031-changing-the-northwind-demo-cardinality-fans-out-to-many-count-assertions.md)).
- **No bundle-schema change / no `schema_version` bump** — two procedures on one control is additive to
  the already-unbounded `workpaper.procedures` array ([learning 0015](../../learnings/0015-verdicts-and-thresholds-are-render-store-only-never-in-the-bundle.md)).
- Not a refactor of any other demo control.

## The two procedures (both fail on existing data)

Both examine the same population — manual journal entries with `|amount| ≥ $50,000` (**5 entries** in the
demo data):

| Proc | Assertion | Flags | Authoring | Exceptions |
| ---- | --------- | ----- | --------- | ---------- |
| **P1 · Independent Review (SoD)** | Segregation of duties | `reviewed_by` is **present and `== prepared_by`** (self-review) | **Custom Python** (column-vs-column — not in the no-code grammar) | 2 (JE-V01, JE-V03) |
| **P2 · Reviewer Assigned** | Authorization / approval evidence | `reviewed_by is_empty` (no reviewer) | **No-code** Test node | 1 (JE-V02) |

The two exception sets are **disjoint**, so the control's **total exceptions stay 3** — the suite's
violation-count assertions (`Finance.GL.1: 3`, grand total `20`) remain valid. Each procedure keeps the
**implicit-zero** threshold (any exception fails — preserving the existing "zero exceptions tolerated"
workpaper line). Overall control verdict is the unchanged any-procedure-fails roll-up → **fails**.

Data breakdown (verified against `data/journal_entries.csv`): of the 5 manual entries with `|amount| ≥
50000`, 2 are self-reviewed (JE-V01, JE-V03), 1 has no reviewer (JE-V02), 2 are clean.

## The graph (`pipeline.yaml`)

A shared trunk forks into two procedure terminals:

```
je       (import journal_entries)
└─ manual    (filter: entry_type eq manual)
   └─ material (filter: any[ amount ge 50000, amount le -50000 ])   # |amount| ≥ 50k, no-code
      ├─ p1_sod    (custom_python test, procedure_id=p1, item_key_column=entry_id)
      │              # flag rows where reviewed_by != "" and reviewed_by == prepared_by
      └─ p2_review (test, procedure_id=p2, item_key_column=entry_id)
                     # condition: reviewed_by is_empty
procedures:
  - {id: p1, code: P1, name: "Independent Review (SoD)", assertion: "Segregation of duties", position: 0}
  - {id: p2, code: P2, name: "Reviewer Assigned",        assertion: "Authorization / approval evidence", position: 1}
```

- `amount` is `data_type: number` in `sources.yaml`, so the no-code `ge`/`le` comparisons match (numeric
  literals, [learning 0011](../../learnings/0011-match-condition-value-type-to-the-columns-loaded-dtype.md)).
  `reviewed_by` is `text`, so `is_empty` (dtype-agnostic) is valid.
- Materiality (`|amount| ≥ 50k`) **must** be a shared upstream Filter: the no-code P2 Test node has a
  single flat all/any logic and cannot nest "(amount≥50k OR ≤-50k) AND reviewed_by empty", and the
  current control's `abs()` semantics (large credits count) require the two-sided `any` group. Both
  procedures therefore evaluate the **post-materiality** set.
- The P1 Custom-Python node stays starved (only `rows`, allowlisted constructs) and emits
  `{item_key: entry_id, description, severity: high, details}` — same contract as the node it replaces,
  minus the missing-reviewer branch (now P2's job).

## Compile / run / workpaper / bundle

- The graph compiles (existing machinery, [learning 0010](../../learnings/0010-new-authoring-representation-compiles-to-the-existing-artifact.md))
  to a **union `test(pop, sources)`** over the two terminals for the single `control.test_code` — the
  bundle never learns "node" or "procedure-graph". No new compile code; this is the multi-terminal path
  the procedures feature already ships.
- Run fans out per-procedure ([learning 0035](../../learnings/0035-fan-out-run-per-procedure-aggregate-only-when-multi-distinct-items-examined.md)):
  Finance.GL.1 now persists 2 per-procedure runs **+ one `procedure_id=''` aggregate** (because
  `len(per_proc_runs) > 1`), where it previously persisted exactly **one** run.
- **Workpaper:** Finance.GL.1 renders **two procedure sections** (`P1 · Independent Review (SoD)` with the
  SoD assertion subtitle; `P2 · Reviewer Assigned`), each with its own distinct-items-examined population
  (= 5) and verdict (both FAIL); the per-check / union-exceptions machinery is the shipped procedures
  renderer.
- **Bundle:** `workpaper.procedures` for Finance.GL.1 goes **1 → 2** — additive, `required` unchanged,
  `additionalProperties` already true → schema-valid, **no `schema_version` bump**. The contract gates
  (`tests/test_contract_export.py`, `tests/schema/test_bundle_schema.py`) must stay green.

## The population shift (own it explicitly — [learning 0035](../../learnings/0035-fan-out-run-per-procedure-aggregate-only-when-multi-distinct-items-examined.md))

Today Finance.GL.1's single test reads the **`manual`** frame (17 rows) — materiality lives **inside** the
Custom-Python node — so its reported population is **17**. After conversion, materiality is an explicit
`material` Filter, so each procedure's **distinct-items-examined** population is **5** (manual `|amount| ≥
50k`). The reported population/records-tested for Finance.GL.1 therefore shifts **17 → 5**. This is the
intended uniform distinct-items-examined semantics (the procedures genuinely examined the 5 material
entries), not a regression. `grep` every demo-referencing test for a `17` (or population/records-tested)
literal tied to this control and update it; assert the new `5` explicitly so the shift is intentional.

## Fan-out to own ([learning 0031](../../learnings/0031-changing-the-northwind-demo-cardinality-fans-out-to-many-count-assertions.md))

- **Regenerate** the committed `examples/northwind-trading/controlplane.db` so the shipped one-click demo
  matches the new pipeline (`examples/` is the single source of truth force-included into the wheel; there
  is **no** separate `controlflow_sdk/_demo/` copy in the tree). Define the regeneration mechanism in the
  plan (fresh migrated db → `import_project(examples/northwind-trading)` → run each control), and verify
  the rebuilt db opens in the control plane with the two-procedure control intact.
- `tests/examples/test_northwind.py`: keep `Finance.GL.1: 3`; **add** an assertion that the built bundle's
  workpaper for Finance.GL.1 carries **2 procedures** with codes `P1`/`P2` and the SoD/authorization
  assertions; **fix the stale `# … unchanged at 18` comment** (the assert is already `== 20`).
- `grep` the demo-referencing test files — `tests/cli/test_build_cmd.py`, `tests/cli/test_import_cmd.py`,
  `tests/cli/test_run_cmd.py`, `tests/store/test_import_service.py`, `tests/pipeline/test_compile.py`,
  `tests/plane/test_pipeline_save.py`, `tests/plane/test_wheel_build.py` — for any assertion pinning
  Finance.GL.1's single-procedure shape, the old population (`17`), run count, or the compiled-`test_code`
  shape, and update each (the compiled code is now a two-terminal union `test()`).
- Update the demo **README** (Finance.GL.1 now two procedures, one no-code + one Custom Python) and
  **`PRODUCT-MAP.md`** row 31 (drop "All demo controls are currently single-procedure (the multi-procedure
  rollup showcase is a deferred follow-up, not built here)" → name Finance.GL.1 as the shipped
  multi-procedure showcase; the demo is no longer "entirely single-procedure").
- Update `control.yaml` — retitle from "Manual Journal Entry Review (Segregation of Duties)" to **"Manual
  Journal Entry Review"** (the parenthetical SoD is now P1's assertion; P2 covers authorization), keep
  `id: Finance.GL.1` and the objective/narrative (the narrative already describes both checks).
- Update `test.py` (the **documentation** sidecar — not the executed artifact once the pipeline sidecar is
  present) to mirror the two-procedure split, so the docs stay honest.

## Testing strategy

- **Equivalence** ([learning 0009](../../learnings/0009-prove-generated-code-equals-the-interpreter.md)): the
  graph's generated union `test()` equals the interpreter over the demo fixture (a multi-terminal /
  multi-procedure case).
- **Split + totals:** P1 flags exactly {JE-V01, JE-V03}, P2 flags {JE-V02}; control total = 3; grand total
  across controls = 20 (unchanged).
- **Population:** Finance.GL.1's per-procedure distinct-items-examined population = 5 (asserted explicitly —
  the 17→5 shift is intentional).
- **Workpaper/bundle:** the built bundle validates; Finance.GL.1's workpaper carries 2 procedures with the
  right codes/assertions; "zero exceptions tolerated" still present (implicit-0 threshold); no raw
  population leaks ([learning 0029](../../learnings/0029-trust-boundary-teeth-check-uses-an-include-false-sentinel-column.md)).
- **Run fan-out** ([learning 0035](../../learnings/0035-fan-out-run-per-procedure-aggregate-only-when-multi-distinct-items-examined.md)):
  if a test asserts Finance.GL.1's run count, it is now 2 per-procedure runs + 1 aggregate (was 1).
- **Demo rebuild:** re-import + run the updated demo end-to-end; the regenerated `controlplane.db` opens
  with the two-procedure control.
- Keep the contract gates, `ruff`, `mypy`, and the full suite green/pristine.

## Rough build sequence

1. Rewrite `controls/manual-je-review/pipeline.yaml` (fork + `procedures:`), retitle `control.yaml`,
   update `test.py` doc sidecar. (TDD against a focused import+compile+run test.)
2. Equivalence + split/population/workpaper/bundle tests (`tests/examples/test_northwind.py` additions;
   fix the stale comment).
3. `grep`-and-update the fan-out across the other demo-referencing test files (population `17`, run count,
   compiled-shape, single-procedure assertions).
4. Regenerate the committed `examples/northwind-trading/controlplane.db`; verify it opens with two
   procedures.
5. README + `PRODUCT-MAP.md` prose.
6. Full sweep: contract gates, ruff, mypy, suite pristine; confirm no `schema_version`/schema diff.

## Open questions

None blocking. (Deferred niceties: a second multi-procedure demo control; an assertion-typeahead — both
out of scope here.)
