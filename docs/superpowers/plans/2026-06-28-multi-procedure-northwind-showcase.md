# Multi-procedure Northwind Showcase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the `northwind-trading` demo's `manual-je-review` control (Finance.GL.1) from a single
Custom-Python procedure into a **two-procedure** control over the same data, so the demo showcases the
procedures rollup + collapsible sections + the authoring ladder.

**Architecture:** Rewrite the control's `pipeline.yaml` into a shared trunk (Import → Filter manual →
Filter materiality) that forks into two terminals — **P1** (self-review, Custom Python) and **P2**
(missing reviewer, no-code) — plus a top-level `procedures:` array. The existing compile/run/bundle
machinery already handles forked multi-terminal pipelines; this is a **demo + tests + docs** change.

**Tech Stack:** YAML pipeline authoring, the `cflow` CLI (`import`/`run`/`build`), pytest, ruff
(py311, line-length 100), mypy.

## EXECUTION RULES

- Never ask the user for permission to continue between tasks. Execute the full plan start to finish.
- On an unresolvable error after 2–3 attempts: note it in the ledger and skip to the next task.
- After every `git commit`, push: `git push -u origin HEAD`.
- Keep `python -m pytest -q`, `ruff check .`, and `mypy controlflow_sdk` green after every task.

## Corrections to the spec discovered during planning (binding)

Two spec assumptions were checked against the code and corrected — the plan reflects reality:

1. **No `controlplane.db` regeneration.** The committed `examples/northwind-trading/controlplane.db` is an
   **empty** placeholder (0 controls / 0 sources / 0 runs — verified). It holds no control data and cannot
   go stale from a YAML change. **Do not** regenerate or touch it. (If a step seems to need it, stop —
   it doesn't.)
2. **No `cli/build_cmd.py` code change.** `cflow build` delegates bundle assembly to
   `store/export_service.build_bundle`, which already builds `procedure_run_map` + `procedure_info_by_control`
   and emits N-procedure workpapers correctly. `build_cmd`'s own `runs_by_control` is used **only** for the
   printed summary counts. So the multi-procedure control builds correctly via the CLI with no code change.

## Global Constraints

- **Cardinal rule (learning 0001):** bundle-additive only. `workpaper.procedures` is already unbounded, so
  two procedures on one control is schema-valid — **no `schema_version` bump**, no edit to
  `contract/bundle.schema.json` / `controlflow_sdk/schema/` / `controlflow_sdk/bundle/`. The contract gates
  (`tests/test_contract_export.py`, `tests/schema/test_bundle_schema.py`) must stay green.
- **No data change:** `examples/northwind-trading/data/journal_entries.csv` is unchanged.
- **No new control/source:** the demo control count stays **9** (every `== 9` assertion stays valid).
- **Total exceptions unchanged:** Finance.GL.1 still flags **3** distinct entries; the grand total across
  controls stays **20**.
- **Item-key:** both terminals key on `entry_id` (the source's single key).
- ruff py311 / line-length 100; mypy clean; pytest pristine (no warnings).

## Verified facts (use these exact values)

- Among manual entries with `|amount| ≥ 50000` (**5 entries**): self-reviewed = **JE-V01, JE-V03** (2),
  missing reviewer = **JE-V02** (1), clean = 2.
- `amount` is `data_type: number` in `sources.yaml` (no-code `ge`/`le` with numeric literals match —
  learning 0011); `reviewed_by` is `text` (`is_empty` is dtype-agnostic).
- After conversion: P1 run `failed=2`, P2 run `failed=1`, control-level **aggregate** run `failed=3`
  (deduped by item-key, disjoint sets); each run's distinct-items-examined `population=5`.
- Run fan-out (learning 0035): Finance.GL.1 persists **3** runs (2 per-procedure + 1 aggregate); the other
  8 controls persist 1 each → the bundle's total run count goes **9 → 11**.

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `examples/northwind-trading/controls/manual-je-review/pipeline.yaml` | The 2-procedure forked graph. | 1 |
| `examples/northwind-trading/controls/manual-je-review/control.yaml` | Retitle (drop the "(Segregation of Duties)" parenthetical). | 1 |
| `examples/northwind-trading/controls/manual-je-review/test.py` | Documentation sidecar — mirror the two-procedure split. | 1 |
| `tests/examples/test_northwind.py` | Aggregate-run selection; run-count 9→11; 2-procedure workpaper assertion; fix stale comments. | 1 |
| Other demo-referencing tests | Grep + adjust anything pinning the old shape (mostly verify-only). | 2 |
| `examples/northwind-trading/README.md` | Finance.GL.1 now two procedures. | 3 |
| `PRODUCT-MAP.md` | Row 31: drop "all single-procedure / deferred"; name Finance.GL.1 as the showcase. | 3 |

---

### Task 1: Convert Finance.GL.1 into two procedures (+ keep its fixture green)

**Files:**
- Rewrite: `examples/northwind-trading/controls/manual-je-review/pipeline.yaml`
- Modify: `examples/northwind-trading/controls/manual-je-review/control.yaml`
- Modify: `examples/northwind-trading/controls/manual-je-review/test.py`
- Modify (test): `tests/examples/test_northwind.py`

**Interfaces:**
- Consumes: the `cflow` CLI (`import`/`run`/`build` via `controlflow_sdk.cli.main`), `repo.list_runs_for`,
  `validate_bundle`. The pipeline model parses a top-level `procedures:` array beside `nodes:`; a Test
  node's owning procedure is `config.procedure_id`; `custom_python` test nodes use `config.flavor: test`.
- Produces: a forked demo control with terminals `sod` (procedure p1) and `review` (procedure p2).

- [ ] **Step 1: Update the fixture assertions FIRST (they will fail against the current single-proc control)**

In `tests/examples/test_northwind.py`:

(a) Make the per-control failed-count read the **control-level aggregate** run (robust to the multi-proc
fan-out — the aggregate has an empty `procedure_id`; single-proc controls fall back to their one run):

```python
    for cid in EXPECTED:
        runs = repo.list_runs_for(conn, cid)
        assert runs, f"No run found in store for control '{cid}'"
        # Multi-procedure controls persist per-procedure runs + a control-level
        # aggregate (empty procedure_id); pick the aggregate for the control's
        # headline failed count. Single-procedure controls have one run → fallback.
        agg = next((r for r in runs if not r.get("procedure_id")), runs[0])
        by_control[cid] = agg["failed"]
```

(b) Fix the two stale comments and keep the totals (Finance.GL.1 stays 3, grand total stays 20):

```python
    # Exactly the 9 expected controls.
    assert set(by_control.keys()) == set(EXPECTED.keys()), (
        f"Unexpected controls in run results: {set(by_control.keys()) ^ set(EXPECTED.keys())}"
    )

    # Total exceptions across the population are unchanged at 20.
    assert sum(by_control.values()) == 20, "Northwind seeded exception total drifted from 20"
```

(c) Update the bundle run-count to the post-fan-out value and add a two-procedure workpaper assertion for
Finance.GL.1 (after the existing `validate_bundle` block):

```python
    assert len(manifest["controls"]) == 9, (
        f"Expected 9 controls in manifest, got {len(manifest['controls'])}"
    )
    # Finance.GL.1 now fans out to 2 per-procedure runs + 1 aggregate = 3; the
    # other 8 controls persist 1 run each (learning 0035) → 11 total.
    assert sum(len(c["runs"]) for c in manifest["controls"]) == 11, (
        "Expected 11 run entries (Finance.GL.1 multi-procedure: 2 per-proc + 1 aggregate)"
    )
    # Finance.GL.1's workpaper now carries TWO procedures (P1 SoD + P2 authorization).
    gl1 = next(c for c in manifest["controls"] if c["id"] == "Finance.GL.1")
    gl1_procs = gl1["workpaper"]["procedures"]
    assert [p["code"] for p in gl1_procs] == ["P1", "P2"], gl1_procs
    assert {p["assertion"] for p in gl1_procs} == {
        "Segregation of duties", "Authorization / approval evidence"
    }, gl1_procs
```

- [ ] **Step 2: Run the fixture to verify it fails**

Run: `python -m pytest tests/examples/test_northwind.py -q`
Expected: FAIL — the current single-procedure control yields 1 procedure / 9 runs, so the new
`["P1","P2"]` and `== 11` assertions fail.

- [ ] **Step 3: Rewrite `pipeline.yaml` into the forked 2-procedure graph**

Replace `examples/northwind-trading/controls/manual-je-review/pipeline.yaml` with:

```yaml
# Visual-pipeline (graph) authoring — a 2-procedure control showcasing the
# procedures rollup over a shared trunk that forks into two terminals.
#
# Finance.GL.1 is performed via two procedures over the same population (manual
# journal entries with |amount| >= $50,000):
#   P1 - Independent Review (Segregation of Duties): a manual JE must be reviewed
#        by someone OTHER than the preparer. Flags self-review (reviewed_by present
#        AND == prepared_by). Column-vs-column => Custom Python (the no-code grammar
#        has no column-vs-column operator).
#   P2 - Reviewer Assigned (Authorization / approval evidence): a manual JE must have
#        a reviewer at all. Flags a missing reviewer (reviewed_by is_empty) => no-code.
#
# Shared trunk: Import journal entries -> Filter to MANUAL -> Filter to materiality
# (|amount| >= $50k, expressed no-code as any[amount >= 50000, amount <= -50000] since
# the grammar has no abs()). The trunk forks into the two procedure terminals.
#
# Store-only; COMPILES to a union test(pop, sources) over the two terminals for the
# single control.test_code (learning 0010) — the bundle never learns "node" or
# "procedure". Expected on demo data: P1 flags 2 (JE-V01, JE-V03), P2 flags 1 (JE-V02);
# union = 3 distinct exceptions; each procedure's distinct-items-examined population = 5.
nodes:
  - id: je
    type: import
    source_id: journal_entries
    narrative: Journal entry register (primary population).
  - id: manual
    type: filter
    inputs: [je]
    narrative: Keep only manually prepared entries.
    config:
      logic: all
      conditions:
        - column: entry_type
          op: eq
          value: manual
  - id: material
    type: filter
    inputs: [manual]
    narrative: Keep entries at or above the $50,000 materiality threshold (absolute value).
    config:
      logic: any
      conditions:
        - column: amount
          op: ge
          value: 50000
        - column: amount
          op: le
          value: -50000
  - id: sod
    type: custom_python
    inputs: [material]
    narrative: >-
      P1 — Segregation of duties: flag material manual entries reviewed by their own preparer.
    config:
      flavor: test
      procedure_id: p1
      item_key_column: entry_id
      severity: high
      code: |
        out = []
        for _idx, _row in rows.iterrows():
            r = _row.to_dict()
            prepared_by = str(r.get("prepared_by") or "").strip()
            reviewed_by = str(r.get("reviewed_by") or "").strip()
            if reviewed_by != "" and reviewed_by == prepared_by:
                out.append({
                    "item_key": str(r.get("entry_id")),
                    "description": "Entry reviewed by preparer (self-authorization)",
                    "severity": "high",
                    "details": {"prepared_by": prepared_by, "reviewed_by": reviewed_by},
                })
        return out
  - id: review
    type: test
    inputs: [material]
    narrative: >-
      P2 — Reviewer assigned: flag material manual entries with no independent reviewer.
    config:
      procedure_id: p2
      item_key_column: entry_id
      severity: high
      description_template: "No independent reviewer assigned to entry {entry_id}"
      logic: all
      conditions:
        - column: reviewed_by
          op: is_empty
procedures:
  - id: p1
    code: P1
    name: Independent Review (SoD)
    assertion: Segregation of duties
    position: 0
  - id: p2
    code: P2
    name: Reviewer Assigned
    assertion: Authorization / approval evidence
    position: 1
```

- [ ] **Step 4: Retitle `control.yaml`**

In `examples/northwind-trading/controls/manual-je-review/control.yaml`, change the title line only (keep
`id: Finance.GL.1`, objective, narrative, framework_refs, risk, sources unchanged — the narrative already
describes both the SoD and the reviewer-assigned checks):

```yaml
title: Manual Journal Entry Review
```

- [ ] **Step 5: Update the `test.py` documentation sidecar to mirror the split**

`test.py` is documentation (not the executed artifact once `pipeline.yaml` is present). Update its module
docstring + body so it reads as the two-procedure control. Replace the file with:

```python
"""Manual journal entry review — two procedures over material manual entries.

Documentation sidecar for Finance.GL.1 (the executed artifact is the visual
pipeline in ``pipeline.yaml``, which forks into two procedure terminals):

  P1 · Independent Review (Segregation of Duties) — a material manual entry
       reviewed by its own preparer (reviewed_by present and == prepared_by).
  P2 · Reviewer Assigned (Authorization)         — a material manual entry with
       no independent reviewer (reviewed_by empty).

"Material" = a manual entry with abs(amount) >= 50000.
"""

import pandas as pd


def test(pop):  # noqa: ANN001, ANN201
    df = pop.df
    violations = []

    for _, row in df.iterrows():
        if str(row.get("entry_type", "")).strip().lower() != "manual":
            continue
        try:
            amount = float(row["amount"])
        except (ValueError, TypeError):
            continue
        if abs(amount) < 50000:  # materiality on absolute value (large credits count too)
            continue

        prepared_by = str(row.get("prepared_by", "") or "").strip()
        reviewed_by = str(row.get("reviewed_by", "") or "").strip()
        reviewer_missing = pd.isna(row.get("reviewed_by")) or reviewed_by == ""
        self_reviewed = not reviewer_missing and reviewed_by == prepared_by

        if self_reviewed:  # P1 · Segregation of duties
            violations.append({
                "item_key": str(row["entry_id"]),
                "description": "Entry reviewed by preparer (self-authorization)",
                "severity": "high",
                "details": {"prepared_by": prepared_by, "reviewed_by": reviewed_by},
            })
        elif reviewer_missing:  # P2 · Reviewer assigned
            violations.append({
                "item_key": str(row["entry_id"]),
                "description": "No independent reviewer assigned",
                "severity": "high",
                "details": {"prepared_by": prepared_by},
            })

    return violations
```

- [ ] **Step 6: Run the fixture to verify it passes**

Run: `python -m pytest tests/examples/test_northwind.py -q`
Expected: PASS. If the run-count is not 11 or the split is wrong, inspect the actual store
(`repo.list_runs_for(conn, "Finance.GL.1")` and each run's `procedure_id`/`failed`/`population_size`) and
reconcile — do NOT change the assertion to an unverified number; confirm the real value matches the
0035 model (2 per-proc + 1 aggregate) before trusting it.

- [ ] **Step 7: Run the contract gates + the full demo-CLI tests**

Run:
```bash
python -m pytest tests/examples/test_northwind.py tests/test_contract_export.py tests/schema/test_bundle_schema.py tests/cli/test_run_cmd.py tests/cli/test_build_cmd.py -q
```
Expected: PASS (the bundle is additive — 2 procedures on one control is schema-valid; the contract gates
stay green). If any fail, they are part of this conversion's fan-out — fix in this task (or, if they
belong to the broad sweep, note them for Task 2).

- [ ] **Step 8: Lint, type-check, commit, push**

```bash
python -m ruff check . && python -m mypy controlflow_sdk
git add examples/northwind-trading/controls/manual-je-review/ tests/examples/test_northwind.py
git commit -m "feat: Finance.GL.1 demo — two procedures (SoD + reviewer-assigned)"
git push -u origin HEAD
```

---

### Task 2: Sweep the other demo-referencing tests

**Files:**
- Verify / modify as needed: `tests/cli/test_build_cmd.py`, `tests/cli/test_import_cmd.py`,
  `tests/cli/test_run_cmd.py`, `tests/store/test_import_service.py`, `tests/plane/test_wheel_build.py`,
  `tests/pipeline/test_compile.py`, `tests/plane/test_pipeline_save.py`.

**Interfaces:**
- Consumes: the converted demo from Task 1. No production code changes.

**Background (expected impact — verify each):** converting (not adding) keeps every control/source count at
9, so the `== 9` assertions are unaffected. `test_compile.py` and `test_pipeline_save.py` build their own
synthetic graphs (not the demo `pipeline.yaml`) — unaffected. `test_run_cmd.py` asserts only file existence
/ non-empty / `executed_at` for Finance.GL.1 — unaffected. The one likely touch is the **build summary
comment** in `test_build_cmd.py` ("9 controls / 9 runs") whose run count is now 11.

- [ ] **Step 1: Grep the demo-referencing tests for assertions the conversion could move**

Run:
```bash
grep -rn "9 runs\|/ 9\|17\b\|population\|records_tested\|len(.*runs\|procedure\|Finance.GL.1" \
  tests/cli/test_build_cmd.py tests/cli/test_import_cmd.py tests/cli/test_run_cmd.py \
  tests/store/test_import_service.py tests/plane/test_wheel_build.py
```
Read each hit and decide: unaffected (control/source count, existence) vs. stale (the run-count `11`,
single-procedure shape, old population `17`).

- [ ] **Step 2: Fix the build-summary run-count comment (and any hard run-count assertion) in `test_build_cmd.py`**

If `test_build_cmd.py` only asserts `"9 controls" in out` (a substring), the assertion still passes — but
update the stale `# … 9 controls / 9 runs` comment to `9 controls / 11 runs`. If any test asserts the run
count (`"9 runs"`, a `== 9` on runs), update it to `11`. Example (adjust to the actual line):

```python
        # Summary line: "  BUNDLE  <path>  9 controls / 11 runs"
        assert "9 controls" in out
```

- [ ] **Step 3: Run the swept files**

Run:
```bash
python -m pytest tests/cli tests/store/test_import_service.py tests/plane/test_wheel_build.py tests/pipeline/test_compile.py tests/plane/test_pipeline_save.py -q
```
Expected: PASS. Update any remaining stale assertion the conversion legitimately moved (confirm the new
value is correct before changing it — learning 0012/0031).

- [ ] **Step 4: Full suite + lint + type-check, commit, push**

```bash
python -m pytest -q && python -m ruff check . && python -m mypy controlflow_sdk
git add tests
git commit -m "test: reconcile demo-referencing tests with multi-procedure Finance.GL.1"
git push -u origin HEAD
```
(If no test files needed changes beyond Task 1, skip the commit and note it in the report.)

---

### Task 3: Docs — README + PRODUCT-MAP

**Files:**
- Modify: `examples/northwind-trading/README.md`
- Modify: `PRODUCT-MAP.md`

**Interfaces:**
- Consumes: the shipped conversion. Docs only.

- [ ] **Step 1: Update the demo README**

In `examples/northwind-trading/README.md`, find the Finance.GL.1 / manual-je-review description and update
it to note it is now a **two-procedure** control: **P1 · Independent Review (Segregation of Duties)** (a
material manual entry reviewed by its preparer — Custom Python) and **P2 · Reviewer Assigned**
(a material manual entry with no reviewer — no-code), both over the manual `|amount| ≥ $50k` population, 3
total exceptions. (Grep the README for `manual-je`, `Finance.GL.1`, or `Segregation` to find the spot; keep
the surrounding prose style.)

- [ ] **Step 2: Update `PRODUCT-MAP.md` row 31**

Replace the sentence "All demo controls are currently **single-procedure** (the multi-procedure rollup
showcase is a deferred follow-up, not built here)." with one naming Finance.GL.1 as the shipped
multi-procedure showcase, e.g.:

```
One control (`manual-je-review`, Finance.GL.1) is a **multi-procedure** showcase — it forks into
**P1 · Segregation of Duties** (Custom Python) and **P2 · Reviewer Assigned** (no-code) over a shared
materiality-filtered trunk, rolling two procedures into one control result.
```

- [ ] **Step 3: Commit, push**

```bash
git add examples/northwind-trading/README.md PRODUCT-MAP.md
git commit -m "docs: demo README + PRODUCT-MAP — Finance.GL.1 multi-procedure showcase"
git push -u origin HEAD
```

---

## Self-Review (checklist for the author)

1. **Spec coverage:** convert Finance.GL.1 into P1 (Custom Python self-review) + P2 (no-code is_empty) over
   a shared materiality trunk (Task 1) ✓; total exceptions stay 3, per-proc 2/1, aggregate 3, population 5
   (Task 1 Steps 1/6) ✓; bundle 2 procedures + run count 9→11 (Task 1) ✓; no schema bump / contract gates
   green (Task 1 Step 7) ✓; demo-test fan-out swept (Task 2) ✓; README + PRODUCT-MAP (Task 3) ✓; the
   spec's controlplane.db-regeneration and any build_cmd change are explicitly **removed** as not needed
   (Corrections section) ✓.
2. **Placeholder scan:** every code/YAML block is complete; commands have expected output.
3. **Type/value consistency:** procedure ids `p1`/`p2`, codes `P1`/`P2`, assertions "Segregation of duties"
   / "Authorization / approval evidence", item-key `entry_id`, exception split 2/1, totals 3/20, runs 11 —
   used identically in `pipeline.yaml` (Task 1 Step 3) and the test assertions (Task 1 Step 1).
