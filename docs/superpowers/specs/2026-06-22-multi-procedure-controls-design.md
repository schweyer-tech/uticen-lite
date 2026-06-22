# Design — Multi-procedure controls (one control, N diverging tests, one workpaper)

Date: 2026-06-22
Status: Approved (brainstorming) — ready for implementation plan
Area: `pipeline/` · `store/` · `model/` · `render/` · `plane/` · `bundle/`

## Problem

An author often has two tests that share the same source + initial filters but **diverge quickly**
afterward (e.g. "posted invoices: (a) must have an approver; (b) must reference a valid PO"). Today a
control's pipeline allows **exactly one terminal Test node** (`pipeline/model.py:_validate_terminal`),
so the only ways to express this are (1) two separate controls that **re-author the shared trunk**
(duplication) or (2) cram both into one Test's conditions (loses per-test pass/fail + narrative). The
author wants **one control whose workpaper shows both tests as distinct procedures**, each with its own
verdict.

## Decisions (settled in brainstorming)

1. **Audit relationship:** *one control, two procedures* — sub-tests of a single control objective,
   rendered together in one workpaper.
2. **Verdict:** *per-procedure thresholds.* Each procedure carries its own threshold and verdict; the
   control rolls up **"any procedure fails ⇒ control fails."**
3. **Surface:** minimal authoring for this feature. The rich "author on the graph" canvas is a
   **separate, later feature (#2)** — explicitly out of scope here.

## Goals / non-goals

**Goals**
- A control's pipeline may **fork into ≥1 terminal Test nodes**; each becomes a workpaper **procedure**.
- Each terminal Test node carries an optional **procedure title** and its **own threshold**
  (`failure_threshold_pct` / `failure_threshold_count`).
- A run produces **one result per procedure** (own population, exceptions, pass_rate).
- The workpaper renders N procedures, each with its **own determination**, plus an **overall control
  verdict = any-fails roll-up**.
- **Single-terminal controls are unchanged** (byte-for-byte graph, identical workpaper/bundle).
- **The bundle contract does not change** (`schema_version` stays `1.0`).

**Non-goals (explicitly deferred)**
- The editable graph canvas / "branch from here" on-diagram affordance (Feature #2).
- Free-form drag-to-connect.
- Per-procedure thresholds in the **bundle** (they stay store/render-side; if the app ever needs them
  serialized, that's a *coordinated* `schema_version` bump — not this feature).
- CLI/YAML authoring of multi-terminal pipelines (pipelines are a plane-only authoring concept; the
  exported bundle still carries one runnable `control.test_code`, see §Compile).

## Why this is contract-safe (the cardinal rule)

Grounded against `contract/bundle.schema.json`:

- `$defs/workpaper/properties/procedures` is an **unbounded array** of `$defs/procedure`
  (`{title, narrative, test_code, result}`) — **no `maxItems`**. N procedures already validate.
- **No `threshold` / `determination` field exists anywhere in the bundle.** The bundle carries only raw
  run results (`$defs/run`: `passed/failed/total/pass_rate/details.violations`). Thresholds and verdicts
  are **control-plane + render concerns** and never cross the bundle boundary.
- The bundle workpaper is built by `bundle/assemble.py:_build_workpaper`, which emits only
  schema-allowed fields. Extending it to emit **N** procedures keeps it valid by construction.
- `control.test_code` stays a single runnable test — we compile a **stitched union** for it (below), so
  the control still has one canonical test for the app, while the per-branch breakdown lives in
  `workpaper.procedures[]`.

Gate: `tests/test_contract_export.py` + `tests/schema/test_bundle_schema.py` must stay green; the e2e
export assertion is the cardinal-rule-0001 guard.

## Architecture (by component)

### 1. Pipeline model — allow N terminals (`controlflow_sdk/pipeline/model.py`)
- `_validate_terminal`: change from *exactly one sink* to *≥1 sinks, **every** sink terminal-capable*
  (a `test`, or a `custom_python` with `flavor == "test"`). A non-terminal sink (e.g. a dangling
  Filter) is still an error with a clear message ("every endpoint must be a Test").
- Add `Pipeline.terminals -> list[Node]` (all terminal sinks, in node-declared order for determinism).
- Keep `Pipeline.terminal` returning `terminals[0]` for back-compat, but **audit and migrate** call
  sites that must consider *all* terminals:
  - `plane/routes/pipeline.py:_diagram` / `_card_vm` — mark **every** terminal box/card `terminal=True`.
  - `pipeline/compile.py` — compile per terminal (below).
  - `model/workpaper.py` — one procedure per terminal (below).
- New `Node` config on terminal `test` nodes (all optional, store-only): `title`,
  `failure_threshold_pct`, `failure_threshold_count`. Parsed leniently (absent → fall back to control
  defaults / `Test {id}`); validated as non-negative numbers when present.

### 2. Compile — per-terminal artifacts + a union (`controlflow_sdk/pipeline/compile.py`)
- Add `compile_pipeline_procedures(pipeline) -> list[CompiledProcedure]`: for each terminal, compile the
  **sub-pipeline** = (that terminal's transitive inputs + the terminal). Reuse the existing single-sink
  compile by treating each terminal as the sole sink of its reachable subgraph. Each `CompiledProcedure`
  carries: `procedure_id` (terminal node id), `title`, `narrative`, `rule_spec | test_code` (pure
  single-source ⇒ rule_spec; cross-source/custom ⇒ test_code), and the per-procedure threshold.
- `compile_pipeline` (existing, single-artifact) now returns the **stitched union** for the *control-
  level* `test_code` (a `test()` that runs every branch and concatenates violations) when there are ≥2
  terminals; for one terminal it is **identical to today** (no stitch). This feeds `control.test_code`
  (bundle + CLI single-artifact path) and keeps the single-terminal path byte-identical.
- Equivalence: ship a generated-vs-interpreter test per learning **0009** for the per-terminal compile
  AND the union (exec the generated `test()` and compare against `evaluate_rule` on a **forked** fixture,
  incl. `any`/`all` + cross-source).

### 3. Store — per-procedure runs (`controlflow_sdk/store/`)
- Migration (store-only, bump `user_version`): `ALTER TABLE runs ADD COLUMN procedure_id TEXT` (default
  `''` = the sole/legacy procedure). `repo.insert_run` / row mapping carry `procedure_id`.
- `_save_pipeline_graph` (`plane/routes/controls.py`): compile **all** procedures; store the graph
  (already store-only) + the **union** `compiled.rule_spec/test_code` on the control row (single column,
  unchanged shape). The per-terminal artifacts are re-derived from the graph at run/assemble time (no
  new control columns) — keeps learning **0010** (graph compiles to the existing artifact).

### 4. Run — one result per procedure (`controlflow_sdk/store/run_service.py`)
- `run_control_in_store`: if the control has a pipeline with **≥2 terminals**, parse the graph, and for
  each terminal compile + run its sub-pipeline → **N `RunRecord`s**, each persisted with its
  `procedure_id`. Otherwise behave exactly as today (one run, `procedure_id=''`).
- Assemble the workpaper from the **N latest runs** (one per procedure) — see §5.
- Evidence/workpaper files: still one `target/workpapers/<id>.{html,md}` per control (it now contains N
  procedures); `target/evidence/<id>-violations.json` is the **union** of all procedures' violations.
- `runner/execute.run_control` stays single-result; the multi-terminal fan-out lives in `run_service`
  (store-backed) so the Pyodide-safe runner core is untouched.

### 5. Workpaper + determination (`controlflow_sdk/model/workpaper.py`, `render/html.py`)
- `Procedure` gains a `threshold: Threshold` and a `determination` property (its own verdict against its
  own result).
- `Workpaper.assemble` gains a multi-procedure path: accept the list of `(procedure_meta, run)` and build
  N `Procedure`s; the single-arg path stays for the one-test case.
- `Workpaper.determination` becomes a **roll-up**: `Effective` iff **every** procedure is effective;
  otherwise the worst/failing state ("any fails"). `records_tested` / `exception_count` still aggregate
  for headline tiles, but the **verdict** is any-fails (not aggregate-pct).
- `render/html.py`: render each procedure as its own section with its **own verdict pill**, plus the
  **overall control verdict**. Keep the existing single-procedure layout when N==1 (no visual change).
- `to_dict()` / `_build_workpaper` (bundle): emit N procedures, each `{title, narrative, test_code,
  result}` — **no threshold/determination in the bundle** (stays valid).

### 6. Builder UI — minimal (`plane/templates/partials/_pipe_node.html`, `plane/routes/`)
- Terminal **Test** card gains: a **Procedure title** input and **threshold** inputs (pct + count),
  serialized into the node config by the existing builder JS.
- Save accepts ≥2 terminals (validation relaxed in §1); the author adds a second Test via the existing
  add/insert affordance and wires its input. (The on-graph "branch" affordance is Feature #2.)
- `_diagram` marks all terminals; the flowchart already lays out branches (multi-lane).

## Data flow

```
author forks graph (≥2 Test terminals, each w/ title+threshold)
   └─ save  → _save_pipeline_graph: parse → lint → compile union (control.test_code)
                                            + store graph (store-only)
run    → run_control_in_store: parse graph → for each terminal:
            compile sub-pipeline → run → RunRecord(procedure_id)  → insert_run
         assemble Workpaper(procedures = latest run per procedure, each w/ own threshold)
         determination = any-fails roll-up
render → N procedure sections + overall verdict  ·  evidence = union of violations
export → bundle: control.test_code = union ; workpaper.procedures[] = N × {title,narrative,test_code,result}
         (schema unchanged — still 1.0)
```

## Testing strategy

- **Model:** `parse_pipeline` accepts a fork with 2 Test terminals; rejects a non-test sink with a clear
  message; `terminals` returns both in order; single-terminal back-compat unchanged.
- **Compile (learning 0009):** per-terminal generated `test()` == `evaluate_rule` on a forked fixture
  (incl. `any`/`all` + a cross-source branch); the union `test()` == concatenation of both branches.
- **Run/store:** a forked control runs → 2 `RunRecord`s with distinct `procedure_id` + correct
  per-branch population/exceptions; migration round-trips an existing DB.
- **Workpaper/determination:** 2 procedures assembled with own thresholds; one branch over-threshold ⇒
  control verdict fails while the passing branch still shows "effective"; single-procedure verdict
  unchanged.
- **Render:** N procedure sections + per-procedure pills + overall verdict; N==1 renders identically to
  today (snapshot/string asserts).
- **Contract (cardinal 0001):** exporting a forked control validates against `bundle.schema.json`
  (`schema_version` still `1.0`); `control.test_code` is the runnable union; `workpaper.procedures` has 2
  entries.
- **e2e (learning 0012):** author a 2-test control in the Builder → run → workpaper shows 2 procedures
  with independent verdicts → export validates.

## Risks / mitigations

- **Hidden `.terminal` assumptions.** Mitigate by auditing every `.terminal` call site (grep) and routing
  "needs all sinks" through `.terminals`.
- **Union-vs-per-procedure population mismatch.** The control-level union result has one aggregate
  population; per-procedure populations live in `workpaper.procedures[].result`. Documented; the rich view
  is the workpaper, the union is the single-artifact fallback.
- **Back-compat.** Every change keeps the N==1 path identical; covered by explicit single-procedure
  regression asserts + the existing suite staying green.

## Follow-up (not this feature)
- **Feature #2 — editable graph canvas:** server-rendered SVG canvas + HTMX node inspector + on-graph
  add/insert/**branch**/connect; drag-to-connect as a later polish. This feature's minimal Test-card
  fields become the inspector content.
