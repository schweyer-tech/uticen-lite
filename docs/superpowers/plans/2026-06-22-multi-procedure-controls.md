# Multi-procedure Controls — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a control's pipeline fork into ≥1 terminal Test nodes, each rendered as its own workpaper procedure with its own threshold and verdict; the control's overall verdict is an "any procedure fails ⇒ fail" roll-up.

**Architecture:** The pipeline graph stays store-only and keeps compiling to existing artifacts (learning 0010). The model allows N terminals; compile gains a per-terminal path (sub-pipeline extraction → reuse `compile_pipeline`) plus a multi-terminal union for the single `control.test_code`; the store keys runs by `procedure_id`; the run service runs each terminal → N results; the workpaper assembles N procedures and rolls up the verdict. The bundle is unchanged (`procedures` is already an unbounded array; thresholds/determinations never enter the bundle).

**Tech Stack:** Pure-Python ≥3.11, Pyodide-safe core (dataclasses + jsonschema; pandas only in `adapters/` and generated code), FastAPI + HTMX + sqlite3 (`[plane]`), pytest, ruff (py311, line-length 100), mypy.

## Global Constraints

- **Cardinal rule (0001):** `contract/bundle.schema.json` MUST stay valid and `schema_version` MUST stay `"1.0"`. Gate: `tests/test_contract_export.py` + `tests/schema/test_bundle_schema.py`.
- **Pyodide-safe core:** `pipeline/`, `model/`, `rules/` import no pandas; pandas only inside generated `test()` strings and `adapters/`.
- **Learning 0010:** the pipeline graph is store-only and compiles to `rule_spec`/`test_code`; never add "node"/"procedure-threshold" to the bundle schema.
- **Learning 0009:** any code path that GENERATES executable code ships an equivalence test (`exec` generated output, compare to `evaluate_rule`) on real fixtures incl. `any`/`all` + cross-source.
- **Learning 0002:** async/writing plane handlers open their own sqlite connection (try/finally); sync GETs use `Depends(get_conn)`.
- **Learning 0012:** re-run + update the e2e browser smoke when an HTMX swap restructures a `plane/` form.
- **Back-compat:** every change keeps the single-terminal (N==1) path byte-identical (graph, compiled artifact, workpaper, bundle).
- **Gates green after every task:** `python -m pytest -q`, `python -m ruff check .`, `python -m mypy controlflow_sdk` — all clean, output pristine.

---

## EXECUTION RULES (read first)

- **Never ask the user for permission to continue between tasks.** Execute the full plan start to finish without interruption.
- **On an unresolvable error after 2–3 attempts:** note it in your progress report and skip to the next task (do not block the whole plan on one task).
- **Push after every `git commit`:**
  ```bash
  git push -u origin HEAD
  ```
- **TDD always:** write the failing test, watch it fail, implement the minimum, watch it pass, run the full gates, commit, push.
- **Keep the suite + ruff + mypy green and output pristine after every task.**

---

## File structure (what changes)

- `controlflow_sdk/pipeline/model.py` — N-terminal validation + `Pipeline.terminals` + terminal config fields. (Task 1)
- `controlflow_sdk/pipeline/compile.py` — `compile_pipeline_procedures()` + multi-terminal union in `_emit_python`. (Task 2)
- `controlflow_sdk/store/migrations.py`, `controlflow_sdk/store/repo.py`, `controlflow_sdk/model/run.py` — `runs.procedure_id`. (Task 3)
- `controlflow_sdk/store/run_service.py` — fan-out: one run per terminal. (Task 4)
- `controlflow_sdk/model/workpaper.py`, `controlflow_sdk/model/control.py` — per-procedure threshold + determination roll-up; multi-procedure `assemble`. (Task 5)
- `controlflow_sdk/render/html.py`, `controlflow_sdk/render/markdown.py` — N procedure sections + per-procedure pills + overall verdict. (Task 6)
- `controlflow_sdk/bundle/assemble.py` — `_build_workpaper` emits N procedures; control `test_code` = union. (Task 7)
- `controlflow_sdk/plane/templates/partials/_pipe_node.html`, `controlflow_sdk/plane/routes/pipeline.py` — Test-card title+threshold fields; mark all terminals; serialize. (Task 8)
- `tests/e2e/test_smoke.py` (or a new `test_multi_procedure.py`) — author 2-test control end-to-end. (Task 9)

---

## Task 1: Pipeline model — allow N terminals + terminal config

**Files:**
- Modify: `controlflow_sdk/pipeline/model.py`
- Test: `tests/pipeline/test_model.py`

**Interfaces:**
- Produces: `Pipeline.terminals -> list[Node]` (all terminal sinks, node-declared order); `Pipeline.terminal` unchanged (returns `terminals[0]`). `parse_pipeline` accepts ≥1 terminal sinks; rejects a non-terminal sink.
- Consumes: nothing new.

- [ ] **Step 1: Write failing tests** in `tests/pipeline/test_model.py`:

```python
def test_pipeline_allows_two_terminal_tests():
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "s"},
        {"id": "flt", "type": "filter", "inputs": ["imp"],
         "config": {"logic": "all", "conditions": [{"column": "status", "op": "eq", "value": "posted"}]}},
        {"id": "a", "type": "test", "inputs": ["flt"],
         "config": {"logic": "all", "conditions": [{"column": "approver", "op": "is_empty"}]}},
        {"id": "b", "type": "test", "inputs": ["flt"],
         "config": {"logic": "all", "conditions": [{"column": "po", "op": "is_empty"}]}},
    ]}
    from controlflow_sdk.pipeline.model import parse_pipeline
    p = parse_pipeline(graph)
    assert [t.id for t in p.terminals] == ["a", "b"]
    assert p.terminal.id == "a"  # back-compat: first terminal

def test_pipeline_rejects_non_test_sink():
    from controlflow_sdk.pipeline.model import parse_pipeline, PipelineError
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "s"},
        {"id": "flt", "type": "filter", "inputs": ["imp"],
         "config": {"logic": "all", "conditions": [{"column": "x", "op": "is_empty"}]}},
        {"id": "tst", "type": "test", "inputs": ["imp"],
         "config": {"logic": "all", "conditions": [{"column": "x", "op": "is_empty"}]}},
    ]}  # flt is a dangling non-test sink
    import pytest
    with pytest.raises(PipelineError, match="endpoint"):
        parse_pipeline(graph)

def test_single_terminal_back_compat_unchanged():
    from controlflow_sdk.pipeline.model import parse_pipeline
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "s"},
        {"id": "tst", "type": "test", "inputs": ["imp"],
         "config": {"logic": "all", "conditions": [{"column": "x", "op": "is_empty"}]}},
    ]}
    p = parse_pipeline(graph)
    assert [t.id for t in p.terminals] == ["tst"]
    assert p.terminal.id == "tst"
```

- [ ] **Step 2: Run, verify they fail** — `python -m pytest tests/pipeline/test_model.py -q` (the two-terminal/non-test-sink cases currently raise the old "exactly one terminal" error).

- [ ] **Step 3: Implement** in `model.py`:
  - Add to `Pipeline`:
    ```python
    @property
    def terminals(self) -> list["Node"]:
        consumed = {src for n in self.nodes for src in n.inputs}
        return [n for n in self.nodes if n.id not in consumed and _is_terminal(n)]
    ```
    and change `terminal` to `return self.terminals[0]`.
  - Replace `_validate_terminal` body:
    ```python
    consumed = {src for n in nodes for src in n.inputs}
    sinks = [n for n in nodes if n.id not in consumed]
    non_terminal = [s for s in sinks if not _is_terminal(s)]
    if non_terminal:
        raise PipelineError(
            "every pipeline endpoint must be a Test (or a custom_python test-flavor) "
            f"node; node {non_terminal[0].id!r} feeds nothing and is not a Test"
        )
    if not any(_is_terminal(s) for s in sinks):
        raise PipelineError("a pipeline needs at least one terminal Test node")
    ```
  - Update the `Pipeline` docstring ("exactly one terminal Test node" → "one or more terminal Test nodes").

- [ ] **Step 4: Run** — `python -m pytest tests/pipeline/test_model.py -q` → PASS; then full gates (`pytest -q`, `ruff check .`, `mypy controlflow_sdk`).

- [ ] **Step 5: Commit + push**

```bash
git add controlflow_sdk/pipeline/model.py tests/pipeline/test_model.py
git commit -m "feat(pipeline): allow N terminal Test nodes (Pipeline.terminals)"
git push -u origin HEAD
```

---

## Task 2: Compile — per-procedure artifacts + multi-terminal union

**Files:**
- Modify: `controlflow_sdk/pipeline/compile.py`
- Test: `tests/pipeline/test_compile.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) CompiledProcedure { procedure_id: str, title: str, narrative: str, result: CompileResult }`
  - `compile_pipeline_procedures(pipeline: Pipeline) -> list[CompiledProcedure]` — one per terminal, in `pipeline.terminals` order.
  - `compile_pipeline(pipeline)` unchanged signature; for ≥2 terminals returns `CompileResult(test_kind="python", test_code=<union>)`; for 1 terminal byte-identical to today.
- Consumes: `Pipeline.terminals` (Task 1).

- [ ] **Step 1: Write failing tests** in `tests/pipeline/test_compile.py` (use the 2-terminal graph from Task 1, parsed):

```python
def _forked():
    from controlflow_sdk.pipeline.model import parse_pipeline
    return parse_pipeline({"nodes": [
        {"id": "imp", "type": "import", "source_id": "inv"},
        {"id": "flt", "type": "filter", "inputs": ["imp"],
         "config": {"logic": "all", "conditions": [{"column": "status", "op": "eq", "value": "posted"}]}},
        {"id": "a", "type": "test", "inputs": ["flt"], "narrative": "approver",
         "config": {"logic": "all", "item_key_column": "id",
                    "conditions": [{"column": "approver", "op": "is_empty"}]}},
        {"id": "b", "type": "test", "inputs": ["flt"], "narrative": "po",
         "config": {"logic": "all", "item_key_column": "id",
                    "conditions": [{"column": "po", "op": "is_empty"}]}},
    ]})

def test_compile_one_procedure_per_terminal():
    from controlflow_sdk.pipeline.compile import compile_pipeline_procedures
    procs = compile_pipeline_procedures(_forked())
    assert [p.procedure_id for p in procs] == ["a", "b"]
    # Procedure "a" is a pure single-source chain → rule_spec referencing approver only.
    a = next(p for p in procs if p.procedure_id == "a")
    assert a.result.test_kind == "rule"
    cols = [c["column"] for c in a.result.rule_spec["conditions"]]
    assert "approver" in cols and "po" not in cols  # b's condition does NOT leak into a

def test_union_test_code_runs_both_branches_and_concatenates(tmp_path):
    # equivalence: exec the union test() over a fixture; violations == branch a + branch b
    import pandas as pd
    from controlflow_sdk.pipeline.compile import compile_pipeline
    from controlflow_sdk.model.population import Population
    union = compile_pipeline(_forked())
    assert union.test_kind == "python"
    ns = {}
    exec(union.test_code, ns)
    df = pd.DataFrame([
        {"id": "1", "status": "posted", "approver": "", "po": "PO1"},   # fails a only
        {"id": "2", "status": "posted", "approver": "X", "po": ""},     # fails b only
        {"id": "3", "status": "draft",  "approver": "", "po": ""},      # filtered out
    ])
    pop = Population(df=df, key_columns=["id"])
    out = ns["test"](pop, {"inv": pop})
    keys = sorted(v["item_key"] for v in out)
    assert keys == ["1", "2"]  # both branches' violations, trunk computed once
```

- [ ] **Step 2: Run, verify fail** — `python -m pytest tests/pipeline/test_compile.py -q` (function not defined / union still single-terminal).

- [ ] **Step 3: Implement** in `compile.py`:
  - **Sub-pipeline extraction** (ancestors of a terminal):
    ```python
    def _subpipeline_for(pipeline: Pipeline, terminal: Node) -> Pipeline:
        keep: dict[str, Node] = {}
        def visit(nid: str) -> None:
            if nid in keep:
                return
            n = pipeline.node(nid)
            keep[nid] = n
            for src in n.inputs:
                visit(src)
        visit(terminal.id)
        # preserve declared order for determinism
        return Pipeline(nodes=[n for n in pipeline.nodes if n.id in keep])
    ```
  - **Per-procedure compile**:
    ```python
    @dataclass(frozen=True)
    class CompiledProcedure:
        procedure_id: str
        title: str
        narrative: str
        result: CompileResult

    def compile_pipeline_procedures(pipeline: Pipeline) -> list[CompiledProcedure]:
        out = []
        for t in pipeline.terminals:
            sub = _subpipeline_for(pipeline, t)
            title = t.config.get("title") or f"Test {t.id}"
            out.append(CompiledProcedure(
                procedure_id=t.id, title=str(title), narrative=t.narrative,
                result=compile_pipeline(sub),
            ))
        return out
    ```
  - **Multi-terminal union** in `_emit_python`: when `len(pipeline.terminals) >= 2`, emit all non-terminal frames once (shared trunk), then for each terminal emit its violations into a uniquely-named `_out_<termid>` list, and `return _out_<t1> + _out_<t2> + ...`. Refactor `_emit_terminal` to take an `out_var` and emit the loop into that var without `return`, then emit a single combined `return`. For 1 terminal keep the exact current output (use `_out` + `return _out`) — guard with `len(terminals) == 1`. Concretely:
    ```python
    terminals = pipeline.terminals
    ...
    for node in order:
        if node.id in {t.id for t in terminals}:
            continue
        body.extend("    " + ln for ln in _emit_node_lines(node, primary_source))
    if len(terminals) == 1:
        body.extend("    " + ln for ln in _emit_terminal(terminals[0], pipeline))
    else:
        for t in terminals:
            body.extend("    " + ln for ln in _emit_terminal(t, pipeline, out_var=f"_out_{t.id}"))
        body.append("    return " + " + ".join(f"_out_{t.id}" for t in terminals))
    ```
    Update `_emit_terminal(node, pipeline, out_var="_out")`: replace `_out`→`out_var`, and emit the trailing `return _out` ONLY when `out_var == "_out"` (single-terminal path); for a custom_python test-flavor terminal in the multi case, emit `{out_var} = _node_<id>(<frame>)` instead of `return ...`.

- [ ] **Step 4: Run** — `python -m pytest tests/pipeline/test_compile.py -q` → PASS; full gates green. Confirm `tests/pipeline/test_compile.py`'s existing single-terminal tests still pass byte-identically (the union path is only taken for ≥2 terminals).

- [ ] **Step 5: Commit + push**

```bash
git add controlflow_sdk/pipeline/compile.py tests/pipeline/test_compile.py
git commit -m "feat(pipeline): compile per-terminal procedures + a multi-terminal union test()"
git push -u origin HEAD
```

---

## Task 3: Store — `runs.procedure_id`

**Files:**
- Modify: `controlflow_sdk/store/migrations.py` (new migration step bumping `user_version`), `controlflow_sdk/store/repo.py` (`insert_run`, run row mapping), `controlflow_sdk/model/run.py` (carry `procedure_id`)
- Test: `tests/store/test_repo_runs.py`

**Interfaces:**
- Produces: `RunRecord.procedure_id: str = ""`; `runs` table column `procedure_id TEXT NOT NULL DEFAULT ''`; `repo.insert_run` persists it; `repo.list_runs`/row mapping reads it.
- Consumes: nothing.

- [ ] **Step 1: Write failing test** in `tests/store/test_repo_runs.py`:

```python
def test_run_persists_procedure_id(tmp_path):
    from controlflow_sdk.store.db import connect
    from controlflow_sdk.store.migrations import migrate
    from controlflow_sdk.store import repo
    from controlflow_sdk.model.run import RunRecord
    conn = connect(tmp_path); migrate(conn)
    repo.upsert_project(conn, name="P")
    repo.upsert_control(conn, id="C", title="t", objective="o", narrative="n",
                        framework_refs={}, test_kind="rule",
                        rule_spec={"logic": "all", "conditions": [{"column": "x", "op": "is_empty"}]})
    run = RunRecord(run_id="r1", control_id="C", executed_at="2026-01-01",
                    population_size=3, violations=[], provenance=[], procedure_id="b")
    repo.insert_run(conn, run)
    rows = repo.list_runs(conn, "C")
    assert rows[0].procedure_id == "b"
```
(Adjust `RunRecord(...)` kwargs / `list_runs` name to the actual API — read `model/run.py` + `repo.py` first.)

- [ ] **Step 2: Run, verify fail** — unknown kwarg `procedure_id` / column missing.

- [ ] **Step 3: Implement**:
  - `model/run.py`: add `procedure_id: str = ""` to `RunRecord` (and its `to_dict`/`from_dict` if present — keep it OUT of the bundle run dict if `$defs/run` forbids extras; see Task 7).
  - `migrations.py`: append a new migration string `ALTER TABLE runs ADD COLUMN procedure_id TEXT NOT NULL DEFAULT '';` (bump the version count the migrator iterates).
  - `repo.py`: `insert_run` includes `procedure_id`; the SELECT/row→`RunRecord` mapping reads it (default `''`).

- [ ] **Step 4: Run** — `python -m pytest tests/store/test_repo_runs.py -q` → PASS; migrate an existing DB test still passes; full gates green.

- [ ] **Step 5: Commit + push**

```bash
git add controlflow_sdk/store/migrations.py controlflow_sdk/store/repo.py controlflow_sdk/model/run.py tests/store/test_repo_runs.py
git commit -m "feat(store): add runs.procedure_id (store-only; default '')"
git push -u origin HEAD
```

---

## Task 4: Run service — one run per procedure

**Files:**
- Modify: `controlflow_sdk/store/run_service.py`
- Test: `tests/store/test_run_service.py`

**Interfaces:**
- Produces: `run_control_in_store(...)` — when the control's pipeline has ≥2 terminals, runs each terminal's sub-pipeline → N `RunRecord`s (distinct `procedure_id`), persists each, assembles a multi-procedure workpaper, writes one workpaper + union evidence. Returns the **union** `RunRecord` (back-compat: callers that expect one record get the aggregate). For N==1, behaves exactly as today.
- Consumes: `compile_pipeline_procedures` (Task 2), `Workpaper.assemble` multi-procedure path (Task 5), `RunRecord.procedure_id` (Task 3).

- [ ] **Step 1: Write failing test** in `tests/store/test_run_service.py` — seed a source CSV + a forked 2-terminal pipeline control, run, assert 2 runs persisted with distinct `procedure_id` and the right per-branch exception counts. (Model it on the existing terminated-access run test; read that file for the seeding helpers.)

```python
def test_forked_control_runs_one_result_per_procedure(tmp_path):
    # ... seed source 'inv' with rows where branch A flags 1 and branch B flags 2 ...
    # save forked pipeline (terminals 'a','b'); run via run_control_in_store
    # assert: repo.list_runs has 2 rows for procedures {'a','b'} with population/violations per branch
    ...
```

- [ ] **Step 2: Run, verify fail** — only one run persisted today.

- [ ] **Step 3: Implement** in `run_service.py`:
  - After loading the control, branch: if `control` has a pipeline that parses to `len(terminals) >= 2`, then for each `CompiledProcedure` build a transient single-test control (the sub-pipeline's compiled `rule_spec`/`test_code`) and run it via the existing `run_control` over the loaded sources; tag each `RunRecord.procedure_id = proc.procedure_id`; `insert_run` each.
  - Assemble the workpaper via the new multi-procedure `Workpaper.assemble` (Task 5), passing the list of `(CompiledProcedure, RunRecord)` with per-procedure thresholds (read from the terminal node config; fall back to the control threshold).
  - Evidence file = union of all procedures' violations.
  - For N==1 keep the existing code path untouched.

- [ ] **Step 4: Run** — task test + full gates green.

- [ ] **Step 5: Commit + push**

```bash
git add controlflow_sdk/store/run_service.py tests/store/test_run_service.py
git commit -m "feat(store): run each pipeline terminal as its own procedure result"
git push -u origin HEAD
```

---

## Task 5: Workpaper + determination roll-up

**Files:**
- Modify: `controlflow_sdk/model/workpaper.py` (`Procedure` gains `threshold` + `determination`; `Workpaper.determination` roll-up; multi-procedure `assemble`)
- Test: `tests/model/test_workpaper.py`

**Interfaces:**
- Produces:
  - `Procedure` gains `threshold: Threshold = field(default_factory=Threshold)` and `@property determination -> Determination` (its own result vs its own threshold).
  - `Workpaper.assemble_procedures(control, procedures: list[tuple[ProcedureSpec, RunRecord]], generated_at, data_samples) -> Workpaper` (or extend `assemble` with an optional `procedures=` list). `ProcedureSpec` = `{title, narrative, test_code, threshold}`.
  - `Workpaper.determination`: **passed iff every procedure passed** (`all(p.determination.passed for p in procedures)`); headline `records_tested`/`exception_count` keep aggregating.
- Consumes: `Threshold`, `Determination` (existing), `CompiledProcedure` metadata (Task 2/4).

- [ ] **Step 1: Write failing tests** in `tests/model/test_workpaper.py`:

```python
def test_control_fails_if_any_procedure_fails():
    from controlflow_sdk.model.workpaper import Workpaper, Procedure
    from controlflow_sdk.model.control import Threshold
    from controlflow_sdk.model.run import RunRecord
    pass_proc = Procedure(title="A", narrative="", test_code="...",
        result=RunRecord(run_id="ra", control_id="C", executed_at="t",
                         population_size=100, violations=[], provenance=[]),
        threshold=Threshold())  # 0 exceptions → effective
    fail_proc = Procedure(title="B", narrative="", test_code="...",
        result=RunRecord(run_id="rb", control_id="C", executed_at="t",
                         population_size=50, violations=[_v(), _v(), _v()], provenance=[]),
        threshold=Threshold())  # 3 exceptions → deficiency
    wp = Workpaper(control_id="C", title="C", objective="", narrative="",
                   framework_refs={}, procedures=[pass_proc, fail_proc])
    assert pass_proc.determination.passed is True
    assert fail_proc.determination.passed is False
    assert wp.determination.passed is False  # any-fails roll-up
```
(`_v()` builds a minimal `Violation`; read `model/violation.py`.)

- [ ] **Step 2: Run, verify fail** — `Procedure` has no `threshold`/`determination`; `Workpaper.determination` currently aggregates counts (a 3/150 = 2% might still "pass" if a pct threshold were set — but with implicit-zero it already fails; pick fixtures so the OLD aggregate logic would *pass* while any-fails *fails*, e.g. set a pct threshold of 5% so aggregate 3/150=2% passes but branch B 3/50=6% fails). Make the test assert any-fails explicitly.

- [ ] **Step 3: Implement** in `workpaper.py`:
  - `Procedure`: add `threshold: Threshold = field(default_factory=Threshold)` and
    ```python
    @property
    def determination(self) -> Determination:
        return Determination(threshold=self.threshold,
                             exception_count=len(self.result.violations),
                             records_tested=self.result.population_size)
    ```
  - `Workpaper.determination`: return a `Determination` whose `passed` is `all(p.determination.passed for p in self.procedures)` — implement as a small roll-up (e.g. keep the aggregate `Determination` for headline numbers but override `passed`/`verdict` via a thin wrapper, OR add `Workpaper.passed` property used by the renderer and conclusion). Keep N==1 identical (one procedure → roll-up == that procedure's determination).
  - Add `Workpaper.assemble` multi-procedure support (new classmethod or `procedures=` param) building `Procedure`s with per-procedure thresholds.

- [ ] **Step 4: Run** — task tests + full gates green; confirm existing single-procedure workpaper tests unchanged.

- [ ] **Step 5: Commit + push**

```bash
git add controlflow_sdk/model/workpaper.py tests/model/test_workpaper.py
git commit -m "feat(workpaper): per-procedure threshold/determination + any-fails roll-up"
git push -u origin HEAD
```

---

## Task 6: Render — N procedure sections + verdicts

**Files:**
- Modify: `controlflow_sdk/render/html.py`, `controlflow_sdk/render/markdown.py`
- Test: `tests/render/test_render.py`, `tests/render/test_html_parity.py`

**Interfaces:**
- Produces: HTML/MD that renders **each** procedure with its own title, narrative, test_code, result table, and **verdict pill**, plus the **overall control verdict**. N==1 renders identically to today.
- Consumes: `Workpaper.procedures[*].determination`, `Workpaper.determination` (Task 5).

- [ ] **Step 1: Write failing test** in `tests/render/test_render.py`: assemble a 2-procedure workpaper (one passing, one failing); assert the HTML contains both procedure titles, two verdict pills (one "effectively", one "deficiencies"), and the overall verdict shows deficiencies. Add an N==1 snapshot assert proving the single-procedure layout is unchanged (string match on the existing structure).

- [ ] **Step 2: Run, verify fail** — only one procedure section rendered.

- [ ] **Step 3: Implement** — read `render/html.py`'s procedure/Results-bar/Conclusion sections; loop over `wp.procedures` to emit a section + per-procedure verdict pill (drive from `procedure.determination`), and render the overall verdict from `wp.determination`. Mirror in `markdown.py`. Keep all colors/markup as-is for N==1.

- [ ] **Step 4: Run** — task tests + `tests/render/test_html_parity.py` + full gates green.

- [ ] **Step 5: Commit + push**

```bash
git add controlflow_sdk/render/ tests/render/
git commit -m "feat(render): render N procedures with per-procedure + overall verdicts"
git push -u origin HEAD
```

---

## Task 7: Bundle — N procedures + union test_code (contract gate)

**Files:**
- Modify: `controlflow_sdk/bundle/assemble.py` (`_build_workpaper`)
- Test: `tests/plane/test_pipeline_editor.py` (new export test) + rely on `tests/test_contract_export.py`, `tests/schema/test_bundle_schema.py`

**Interfaces:**
- Produces: a forked control's bundle entry has `control.test_code` = the **union** test() and `workpaper.procedures` = N × `{title, narrative, test_code, result}`; validates against `bundle.schema.json` (`schema_version` `"1.0"`).
- Consumes: per-procedure runs (Task 4), `compile_pipeline_procedures` (Task 2).

- [ ] **Step 1: Write failing test** (plane test client): seed + save a forked pipeline control, run it, export the bundle, assert: `manifest["schema_version"] == "1.0"`, `validate_bundle(manifest) == []`, the forked control's `workpaper["procedures"]` has length 2 with distinct titles, and `control["test_code"]` is non-empty (the union).

- [ ] **Step 2: Run, verify fail** — only one procedure in the bundle.

- [ ] **Step 3: Implement** — `_build_workpaper`: group the control's runs by `procedure_id` (latest per procedure); emit one `procedure` dict per group with `{title, narrative, test_code, result}` (per-procedure `test_code` = the procedure's rendered rule/text, mirroring `run_service`'s resolution). Ensure NO threshold/determination keys leak into the procedure/workpaper dict. Keep N==1 output identical.

- [ ] **Step 4: Run** — new test + `tests/test_contract_export.py` + `tests/schema/test_bundle_schema.py` + full gates green.

- [ ] **Step 5: Commit + push**

```bash
git add controlflow_sdk/bundle/assemble.py tests/plane/test_pipeline_editor.py
git commit -m "feat(bundle): emit N workpaper procedures for forked controls (schema 1.0 intact)"
git push -u origin HEAD
```

---

## Task 8: Builder UI — Test-card fields + relaxed save + all-terminals

**Files:**
- Modify: `controlflow_sdk/plane/templates/partials/_pipe_node.html` (Test card: procedure title + threshold inputs), `controlflow_sdk/plane/templates/logic_builder.html` (serialize the new fields in the builder JS), `controlflow_sdk/plane/routes/pipeline.py` (`_diagram`/`_card_vm` mark **all** terminals)
- Test: `tests/plane/test_pipeline_editor.py`

**Interfaces:**
- Produces: the Test card renders/serializes `config.title`, `config.failure_threshold_pct`, `config.failure_threshold_count`; saving a 2-terminal graph succeeds (validation relaxed in Task 1); the flowchart marks all terminals.
- Consumes: Task 1 (parse accepts N terminals), Task 7 wiring.

- [ ] **Step 1: Write failing tests** in `tests/plane/test_pipeline_editor.py`:
  - GET builder for a forked control → both Test cards render (`data-node="a"` and `data-node="b"`), and the Test card exposes `data-proc-title`, `data-threshold-pct`, `data-threshold-count` inputs.
  - POST a forked graph (2 terminals, each with a `title`+threshold) → 303 (saved, not 422); GET back shows both titles.
  - `_diagram` marks both terminal boxes `terminal=True` (two `fc-terminal` rects in the flowchart for a forked control).

- [ ] **Step 2: Run, verify fail** — fields absent; current `_card_vm`/`_diagram` mark only `terminals[0]`.

- [ ] **Step 3: Implement**:
  - `_pipe_node.html`: under the Test block add Procedure title + threshold (pct/count) inputs with `data-proc-title`/`data-threshold-pct`/`data-threshold-count`.
  - `logic_builder.html` JS `serialize()`: for `type === 'test'`, read those three into `node.config.title`/`failure_threshold_pct`/`failure_threshold_count` (coerce numbers; empty → omit).
  - `routes/pipeline.py`: `_diagram` and `_card_vm` mark terminal via `node.id in {t.id for t in pipeline.terminals}` (not `== pipeline.terminal.id`).

- [ ] **Step 4: Run** — task tests + full gates green.

- [ ] **Step 5: Commit + push**

```bash
git add controlflow_sdk/plane/templates/partials/_pipe_node.html controlflow_sdk/plane/templates/logic_builder.html controlflow_sdk/plane/routes/pipeline.py tests/plane/test_pipeline_editor.py
git commit -m "feat(plane): author per-procedure title+threshold; mark all terminals"
git push -u origin HEAD
```

---

## Task 9: e2e browser smoke — author a 2-test control (learning 0012)

**Files:**
- Modify/Create: `tests/e2e/test_smoke.py` (add a forked-control case) or `tests/e2e/test_multi_procedure.py`
- Test: itself (`pytest tests/e2e -m browser`)

**Interfaces:**
- Consumes: the full stack (Tasks 1–8).

- [ ] **Step 1: Write the test** — drive the live Builder: upload a CSV, create a control, author an Import → Filter → two Test terminals (add the 2nd Test via the existing add/insert affordance; wire its input to the filter; set each Test's title + a condition + threshold), Save, Run, assert the run/workpaper shows **two procedures with independent verdicts**, then Export and assert `validate_bundle(manifest) == []` and `len(workpaper.procedures) == 2`.

- [ ] **Step 2: Run** — `python -m pytest tests/e2e -m browser -q` → PASS (needs `playwright install chromium`).

- [ ] **Step 3: Commit + push**

```bash
git add tests/e2e/
git commit -m "test(e2e): author/run/export a 2-procedure control end-to-end"
git push -u origin HEAD
```

---

## Final verification (after all tasks)

- [ ] `python -m pytest -q` — all green, output pristine.
- [ ] `python -m ruff check .` — clean. `python -m mypy controlflow_sdk` — clean.
- [ ] `python -m pytest tests/e2e -m browser -q` — green.
- [ ] Manually (or via the seed-and-serve harness) author a forked control and eyeball the workpaper: two procedure sections, two verdicts, overall any-fails verdict.
- [ ] Open a PR (base `main`); body summarizes the feature + the contract-safety argument (schema 1.0 intact). Then update `PRODUCT-MAP.md` (Logic surface now supports multi-procedure controls) and capture any durable learning via `compounding-learnings`.

## Self-review notes (author)
- **Spec coverage:** model (T1), compile incl. 0009 equivalence (T2), store (T3), run (T4), workpaper/determination (T5), render (T6), bundle/contract (T7), UI (T8), e2e/0012 (T9) — all spec sections mapped.
- **Type consistency:** `Pipeline.terminals`, `CompiledProcedure{procedure_id,title,narrative,result}`, `RunRecord.procedure_id`, `Procedure.threshold/determination` used consistently across tasks.
- **Back-compat:** every task carries an explicit N==1 unchanged assertion.
