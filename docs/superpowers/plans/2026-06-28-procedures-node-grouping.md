# Procedures as a Node-Grouping Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an author define first-class **procedures** (code · name · assertion · threshold) on a control's Logic graph and assign Test nodes to them, where one procedure rolls up several checks into a single audit result using distinct-items-examined math.

**Architecture:** Procedures live inside the existing store-only `controls.pipeline` JSON blob (a `procedures` array beside `nodes`); Test nodes carry `config["procedure_id"]`; support-node membership is derived. Compile keys off the procedure definitions (reusing the existing terminal-closure slicing + union emit); the run path runs each check, merges by item-key, and reports distinct-examined population. Workpaper/bundle gain additive-optional `code`/`assertion`. No store migration, no bundle `schema_version` bump.

**Tech Stack:** Python ≥3.11 (Pyodide-safe core: dataclasses + jsonschema, pandas only in `adapters/`/`pipeline` materialize), FastAPI + HTMX + `sqlite3` under `[plane]`, pytest, ruff (py311, line-length 100), mypy.

## Global Constraints

- **Cardinal contract:** `contract/bundle.schema.json` is the one integration surface. This change is **additive-optional only** (new optional `code`/`assertion` on the workpaper `procedure` object; `required` unchanged; `additionalProperties` already `true`) → **no `schema_version` bump**. Update both the SDK copy and `contract/` copy if both exist. Gate: `tests/test_contract_export.py` + `tests/schema/test_bundle_schema.py` must stay green. (learning 0001)
- **Never put raw population in the bundle** (trust boundary). Per-procedure threshold/verdict/color stay render+store-only (learning 0015).
- **No store-schema migration:** procedures ride inside the existing `pipeline` TEXT blob; do **not** bump store `SCHEMA_VERSION` (learning 0022). Old blobs lacking `procedures` derive the default one-per-terminal at read time.
- **Pyodide-safe core:** `model/`, `pipeline/compile.py` stay pandas-free; pandas only in `pipeline/materialize.py` and adapters. Custom Python nodes stay file/source-starved (allowlist AST lint + lexical starvation + export gate — learning 0008).
- **Gates per task:** `python -m pytest -q` pristine (no stray warnings), `python -m ruff check .` clean, `python -m mypy uticen_lite` clean.
- **Determinism:** any serialization the bundle depends on uses `json.dumps(..., sort_keys=True)` (learning 0028); procedures ordered by `position`.
- **Graceful degradation, never 500:** unassigned tests, missing item-keys, incomplete graphs degrade (learnings 0013, 0033).

---

## EXECUTION RULES

- **Never ask the user for permission to continue between tasks.** Execute the full plan start to finish without interruption.
- On an unresolvable error after 2–3 attempts: note it inline in your progress report and **skip to the next task** (don't block the whole plan on one stuck task).
- **Push after every commit.** Every task's final step is:
  ```bash
  git push -u origin HEAD
  ```
  (No project-specific post-push command for this Python repo.)
- Keep the dev gates green at every commit: `python -m pytest -q`, `python -m ruff check .`, `python -m mypy uticen_lite`.

---

## File Structure

| File | Responsibility | Action |
| ---- | -------------- | ------ |
| `uticen_lite/pipeline/model.py` | `ProcedureDef` dataclass + `Pipeline.procedures` field + parse/round-trip | Modify |
| `uticen_lite/pipeline/procedures.py` | Pure helpers: effective procedures (defined-or-derived), tests-for-procedure, derived support-node membership | **Create** |
| `uticen_lite/pipeline/compile.py` | Terminal-**set** closure slice; `compile_pipeline_procedures` keyed off procedure defs; `CompiledProcedure` gains code/assertion | Modify |
| `uticen_lite/store/run_service.py` | Per-check runs + merge-by-item-key; distinct-examined population; `ProcedureSpec` with code/assertion | Modify |
| `uticen_lite/model/workpaper.py` | `ProcedureSpec`/`Procedure` gain `code`+`assertion`; `Procedure.to_dict()` emits them | Modify |
| `uticen_lite/render/html.py`, `render/markdown.py` | Render code · name, assertion subtitle, per-check breakdown, union exceptions table | Modify |
| `uticen_lite/schema/bundle.schema.json` (+ `contract/bundle.schema.json`) | Add optional `code`/`assertion` to `$defs/procedure` | Modify |
| `uticen_lite/bundle/assemble.py` | Thread `code`/`assertion` through `procedure_info_by_control` | Modify |
| `uticen_lite/plane/routes/pipeline.py` | `_editor_context`: procedures + derived colors; `save_pipeline`: accept procedures; `_diagram`: per-procedure color | Modify |
| `uticen_lite/plane/templates/partials/_procedures_panel.html` | The Procedures definition panel | **Create** |
| `uticen_lite/plane/templates/partials/_pipe_node.html` | Per-Test "Procedure ▾" selector + derived color chip | Modify |
| `uticen_lite/plane/templates/logic_builder.html` | Serialize procedures + per-Test `procedure_id`; render panel | Modify |
| `uticen_lite/plane/templates/partials/_pipe_diagram.html`, `logic_flowchart.html` | Per-procedure box color + legend | Modify |
| `examples/northwind-trading/**`, `README.md`, `PRODUCT-MAP.md` | Uniform-population fan-out (learning 0031) | Modify |

---

## Task 1: Pipeline model — `ProcedureDef` + `Pipeline.procedures` + helpers

**Files:**
- Modify: `uticen_lite/pipeline/model.py`
- Create: `uticen_lite/pipeline/procedures.py`
- Test: `tests/pipeline/test_procedures_model.py` (create)

**Interfaces:**
- Produces:
  - `ProcedureDef(id: str, code: str = "", name: str = "", assertion: str = "", narrative: str = "", failure_threshold_pct: float | None = None, failure_threshold_count: int | None = None, position: int = 0)` (frozen dataclass in `model.py`)
  - `Pipeline.procedures: list[ProcedureDef]` (new field, default `[]`)
  - `parse_pipeline(raw)` now parses `raw.get("procedures", [])`
  - In `procedures.py`:
    - `effective_procedures(pipeline: Pipeline) -> list[ProcedureDef]` — the defined procedures sorted by `position`, or a derived one-per-terminal default when none are defined (and an appended auto-procedure per unassigned terminal).
    - `tests_for_procedure(pipeline: Pipeline, procedure_id: str) -> list[Node]` — terminals owning that procedure (default mode: the single matching terminal).
    - `derived_membership(pipeline: Pipeline) -> dict[str, set[str]]` — `{node_id: {procedure_id, …}}`, support nodes included via downstream-test closure.

- [ ] **Step 1: Write the failing test**

Create `tests/pipeline/test_procedures_model.py`:

```python
from uticen_lite.pipeline.model import ProcedureDef, parse_pipeline
from uticen_lite.pipeline.procedures import (
    derived_membership,
    effective_procedures,
    tests_for_procedure,
)


def _two_proc_pipeline() -> dict:
    # Shared Import → two filtered branches, each with one Test, grouped into P1 and P2.
    return {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "je"},
            {"id": "f1", "type": "filter", "inputs": ["imp"],
             "config": {"logic": "all", "conditions": [{"column": "kind", "op": "eq", "value": "manual"}]}},
            {"id": "t1", "type": "test", "inputs": ["f1"],
             "config": {"logic": "all", "item_key_column": "je_id", "procedure_id": "p1",
                        "conditions": [{"column": "preparer", "op": "eq", "value": "approver"}]}},
            {"id": "f2", "type": "filter", "inputs": ["imp"],
             "config": {"logic": "all", "conditions": [{"column": "posted", "op": "eq", "value": "late"}]}},
            {"id": "t2", "type": "test", "inputs": ["f2"],
             "config": {"logic": "all", "item_key_column": "je_id", "procedure_id": "p2",
                        "conditions": [{"column": "posted", "op": "eq", "value": "late"}]}},
        ],
        "procedures": [
            {"id": "p1", "code": "P1", "name": "Manual JE Review", "assertion": "Segregation of Duties",
             "failure_threshold_count": 0, "position": 0},
            {"id": "p2", "code": "P2", "name": "Late Posting", "assertion": "Cutoff",
             "failure_threshold_pct": 1.0, "position": 1},
        ],
    }


def test_parse_round_trips_procedures():
    p = parse_pipeline(_two_proc_pipeline())
    assert [pr.code for pr in p.procedures] == ["P1", "P2"]
    assert p.procedures[0].assertion == "Segregation of Duties"
    assert p.procedures[1].failure_threshold_pct == 1.0


def test_effective_procedures_uses_defined_sorted_by_position():
    p = parse_pipeline(_two_proc_pipeline())
    eff = effective_procedures(p)
    assert [pr.id for pr in eff] == ["p1", "p2"]
    assert [t.id for t in tests_for_procedure(p, "p1")] == ["t1"]
    assert [t.id for t in tests_for_procedure(p, "p2")] == ["t2"]


def test_derived_membership_marks_shared_import_in_both():
    p = parse_pipeline(_two_proc_pipeline())
    mem = derived_membership(p)
    assert mem["imp"] == {"p1", "p2"}     # shared support node feeds both
    assert mem["f1"] == {"p1"}
    assert mem["t1"] == {"p1"}
    assert mem["f2"] == {"p2"}


def test_no_procedures_defined_derives_one_per_terminal():
    raw = _two_proc_pipeline()
    raw.pop("procedures")
    for n in raw["nodes"]:
        n.get("config", {}).pop("procedure_id", None)
    p = parse_pipeline(raw)
    eff = effective_procedures(p)
    assert [pr.code for pr in eff] == ["P1", "P2"]      # auto one-per-terminal
    assert {t.id for t in tests_for_procedure(p, eff[0].id)} == {"t1"}


def test_unassigned_test_falls_back_to_auto_procedure():
    raw = _two_proc_pipeline()
    # Drop t2's procedure_id but keep p1/p2 defined → t2 is unassigned.
    for n in raw["nodes"]:
        if n["id"] == "t2":
            n["config"].pop("procedure_id")
    p = parse_pipeline(raw)
    eff = effective_procedures(p)
    # p1, p2 defined + one auto procedure owning the orphan t2.
    owners = {t.id for pid in (pr.id for pr in eff) for t in tests_for_procedure(p, pid)}
    assert {"t1", "t2"}.issubset(owners)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/pipeline/test_procedures_model.py -q`
Expected: FAIL — `ImportError: cannot import name 'ProcedureDef'` / `procedures` module missing.

- [ ] **Step 3: Add `ProcedureDef` + `Pipeline.procedures` + parsing in `model.py`**

In `uticen_lite/pipeline/model.py`, add the dataclass near `Node` (after the `Node` definition):

```python
@dataclass(frozen=True)
class ProcedureDef:
    """A first-class, author-defined procedure grouping over a control's Test nodes.

    Stored inside the (store-only) pipeline JSON beside ``nodes``. ``id`` is the
    stable internal key Test nodes reference via ``config["procedure_id"]``.
    Threshold/verdict are render+store-only; never serialized to the bundle (0015).
    """

    id: str
    code: str = ""
    name: str = ""
    assertion: str = ""
    narrative: str = ""
    failure_threshold_pct: float | None = None
    failure_threshold_count: int | None = None
    position: int = 0
```

Add the field to `Pipeline` (after `nodes`):

```python
@dataclass(frozen=True)
class Pipeline:
    """An ordered DAG of :class:`Node` with one or more terminal Test nodes."""

    nodes: list[Node]
    procedures: list[ProcedureDef] = field(default_factory=list)
```

Add a parser helper and thread it through `parse_pipeline` (return statement only changes to pass `procedures`):

```python
def _parse_procedure(rp: dict) -> ProcedureDef:
    if not isinstance(rp, dict) or not rp.get("id"):
        raise PipelineError("each procedure needs an 'id'")
    return ProcedureDef(
        id=str(rp["id"]),
        code=str(rp.get("code", "")),
        name=str(rp.get("name", "")),
        assertion=str(rp.get("assertion", "")),
        narrative=str(rp.get("narrative", "")),
        failure_threshold_pct=rp.get("failure_threshold_pct"),
        failure_threshold_count=rp.get("failure_threshold_count"),
        position=int(rp.get("position", 0)),
    )
```

In `parse_pipeline`, after the nodes are validated and before `return Pipeline(...)`:

```python
    raw_procs = raw.get("procedures", [])
    if not isinstance(raw_procs, list):
        raise PipelineError("'procedures' must be a list when present")
    procedures = [_parse_procedure(rp) for rp in raw_procs]
    proc_ids = {p.id for p in procedures}
    if len(proc_ids) != len(procedures):
        raise PipelineError("duplicate procedure id")
    # Test nodes may reference a procedure; an unknown ref is tolerated (degrades to
    # an auto procedure at compile/run) — do NOT raise (learning 0013).
    return Pipeline(nodes=nodes, procedures=procedures)
```

(Confirm `field` is already imported from `dataclasses` at the top of `model.py`; it is, since `Node` uses `field(default_factory=...)`.)

- [ ] **Step 4: Create `uticen_lite/pipeline/procedures.py`**

```python
"""Pure (pandas-free) helpers turning a :class:`Pipeline`'s procedure defs +
Test-node assignments into the effective procedure list, the tests each owns,
and per-node derived membership. No store, no render, no pandas — Pyodide-safe."""

from __future__ import annotations

from uticen_lite.pipeline.model import Node, Pipeline, ProcedureDef


def _ancestors(pipeline: Pipeline, node_id: str) -> set[str]:
    """All ancestor node ids of *node_id* (inclusive)."""
    keep: set[str] = set()

    def visit(nid: str) -> None:
        if nid in keep:
            return
        keep.add(nid)
        for src in pipeline.node(nid).inputs:
            visit(src)

    visit(node_id)
    return keep


def _assigned_procedure_id(test: Node) -> str | None:
    pid = test.config.get("procedure_id")
    return str(pid) if pid else None


def effective_procedures(pipeline: Pipeline) -> list[ProcedureDef]:
    """The procedures actually used for compile/run/render.

    - When the pipeline defines procedures: those (sorted by ``position``), plus an
      appended auto procedure for every terminal whose ``procedure_id`` is unset or
      dangling (graceful degradation — never drop a test).
    - When none are defined: one auto procedure per terminal (today's behavior),
      coded ``P1..Pn`` in terminal order.
    """
    terminals = pipeline.terminals
    defined = sorted(pipeline.procedures, key=lambda p: p.position)
    defined_ids = {p.id for p in defined}

    assigned: dict[str, str] = {}
    orphans: list[Node] = []
    for t in terminals:
        pid = _assigned_procedure_id(t)
        if pid and pid in defined_ids:
            assigned[t.id] = pid
        else:
            orphans.append(t)

    out: list[ProcedureDef] = []
    if defined:
        out.extend(defined)
        start = len(defined)
    else:
        start = 0

    for i, t in enumerate(orphans):
        out.append(_auto_procedure(t, start + i))
    return out


def _auto_procedure(terminal: Node, position: int) -> ProcedureDef:
    return ProcedureDef(
        id=terminal.id,
        code=f"P{position + 1}",
        name=terminal.config.get("title") or terminal.title or f"Test {terminal.id}",
        assertion="",
        narrative=terminal.narrative,
        failure_threshold_pct=terminal.config.get("failure_threshold_pct"),
        failure_threshold_count=terminal.config.get("failure_threshold_count"),
        position=position,
    )


def tests_for_procedure(pipeline: Pipeline, procedure_id: str) -> list[Node]:
    """Terminals owned by *procedure_id*, in declared order.

    For an auto procedure (id == a terminal id and not a defined procedure), the
    owner is exactly that terminal (so unassigned/legacy terminals each map to self).
    """
    defined_ids = {p.id for p in pipeline.procedures}
    if procedure_id in defined_ids:
        return [
            t for t in pipeline.terminals
            if _assigned_procedure_id(t) == procedure_id
        ]
    # Auto procedure: the terminal whose id is the procedure id.
    return [t for t in pipeline.terminals if t.id == procedure_id]


def derived_membership(pipeline: Pipeline) -> dict[str, set[str]]:
    """``{node_id: {procedure_id, …}}`` — a support node belongs to the union of
    procedures of the terminals in its downstream closure. Computed by walking each
    effective procedure's terminals' ancestor closures."""
    out: dict[str, set[str]] = {n.id: set() for n in pipeline.nodes}
    for proc in effective_procedures(pipeline):
        for t in tests_for_procedure(pipeline, proc.id):
            for nid in _ancestors(pipeline, t.id):
                out[nid].add(proc.id)
    return out
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `python -m pytest tests/pipeline/test_procedures_model.py -q`
Expected: PASS (5 tests). Then `python -m ruff check uticen_lite/pipeline` and `python -m mypy uticen_lite/pipeline` clean.

- [ ] **Step 6: Confirm existing pipeline tests still pass (round-trip safety)**

Run: `python -m pytest tests/pipeline -q`
Expected: PASS — no existing pipeline test regresses (procedures default to `[]`).

- [ ] **Step 7: Commit + push**

```bash
git add uticen_lite/pipeline/model.py uticen_lite/pipeline/procedures.py tests/pipeline/test_procedures_model.py
git commit -m "feat(pipeline): first-class ProcedureDef + procedure-membership helpers" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>" \
  -m "Claude-Session: https://claude.ai/code/session_013JpkfZUEVoutkGJAYHqrKJ"
git push -u origin HEAD
```

---

## Task 2: Compile — procedure-keyed compilation over a terminal-set closure

**Files:**
- Modify: `uticen_lite/pipeline/compile.py`
- Test: `tests/pipeline/test_compile_procedures.py` (create)

**Interfaces:**
- Consumes: `effective_procedures`, `tests_for_procedure` (Task 1); existing `compile_pipeline`, `_emit_python`, `_subpipeline_for`.
- Produces:
  - `_subpipeline_for_terminals(pipeline: Pipeline, terminals: list[Node]) -> Pipeline` — union of ancestor closures of all given terminals, declared order preserved.
  - `CompiledProcedure` gains `code: str = ""` and `assertion: str = ""` (keep `procedure_id`, `title`, `narrative`, `result`).
  - `compile_pipeline_procedures(pipeline)` returns one `CompiledProcedure` per **effective procedure** (its `result` compiled from the union subpipeline of its tests).

- [ ] **Step 1: Write the failing test**

Create `tests/pipeline/test_compile_procedures.py`:

```python
from uticen_lite.pipeline.compile import compile_pipeline_procedures
from uticen_lite.pipeline.model import parse_pipeline
from uticen_lite.rules.evaluate import evaluate_rule  # canonical interpreter
from uticen_lite.model.population import Population
import pandas as pd


def _multi_check_one_procedure() -> dict:
    # ONE procedure (p1) owning TWO checks (t1, t2) over the same filtered trunk.
    return {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "je"},
            {"id": "flt", "type": "filter", "inputs": ["imp"],
             "config": {"logic": "all", "conditions": [{"column": "kind", "op": "eq", "value": "manual"}]}},
            {"id": "t1", "type": "test", "inputs": ["flt"], "title": "preparer=approver",
             "config": {"logic": "all", "item_key_column": "je_id", "procedure_id": "p1",
                        "conditions": [{"column": "preparer", "op": "eq", "value": "approver"}]}},
            {"id": "t2", "type": "test", "inputs": ["flt"], "title": "no approval",
             "config": {"logic": "all", "item_key_column": "je_id", "procedure_id": "p1",
                        "conditions": [{"column": "approval", "op": "is_empty"}]}},
        ],
        "procedures": [
            {"id": "p1", "code": "P1", "name": "Manual JE Review",
             "assertion": "Segregation of Duties", "position": 0},
        ],
    }


def test_one_compiled_procedure_carries_metadata():
    p = parse_pipeline(_multi_check_one_procedure())
    procs = compile_pipeline_procedures(p)
    assert len(procs) == 1
    assert procs[0].procedure_id == "p1"
    assert procs[0].code == "P1"
    assert procs[0].title == "Manual JE Review"
    assert procs[0].assertion == "Segregation of Duties"


def test_compiled_union_matches_interpreter(tmp_path):
    """The generated union test() must equal the union of the interpreter over the
    procedure's checks (learning 0009)."""
    p = parse_pipeline(_multi_check_one_procedure())
    procs = compile_pipeline_procedures(p)
    result = procs[0].result  # CompileResult: union test() string for the 2 checks

    df = pd.DataFrame([
        {"je_id": "A", "kind": "manual", "preparer": "approver", "approval": ""},   # fails both
        {"je_id": "B", "kind": "manual", "preparer": "alice", "approval": ""},       # fails t2
        {"je_id": "C", "kind": "manual", "preparer": "approver", "approval": "yes"}, # fails t1
        {"je_id": "D", "kind": "auto",   "preparer": "approver", "approval": ""},     # filtered out
    ])
    pop = Population(df=df, key_columns=["je_id"])

    ns: dict = {}
    exec(result.test_code, ns)  # noqa: S102 — generated, guardrailed
    generated = ns["test"](pop, {})
    keys = sorted(v["item_key"] for v in generated)
    # A (both), B (t2), C (t1) ⇒ keys A,A,B,C before dedupe (concat of both checks)
    assert keys == ["A", "A", "B", "C"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/pipeline/test_compile_procedures.py -q`
Expected: FAIL — `CompiledProcedure` has no `code`/`assertion`, and `compile_pipeline_procedures` keys off terminals (returns 2, not 1).

- [ ] **Step 3: Add the terminal-set closure + extend `CompiledProcedure`**

In `uticen_lite/pipeline/compile.py`, refactor `_subpipeline_for` to delegate to a set-based variant and add the new function:

```python
def _subpipeline_for_terminals(pipeline: Pipeline, terminals: list[Node]) -> Pipeline:
    """Sub-pipeline = the union of the ancestor closures of *terminals*.

    Declared node order is preserved for determinism. Procedure defs are dropped
    from the slice (the slice compiles to violations only)."""
    keep: set[str] = set()

    def visit(nid: str) -> None:
        if nid in keep:
            return
        keep.add(nid)
        for src in pipeline.node(nid).inputs:
            visit(src)

    for t in terminals:
        visit(t.id)
    return Pipeline(nodes=[n for n in pipeline.nodes if n.id in keep])


def _subpipeline_for(pipeline: Pipeline, terminal: Node) -> Pipeline:
    """Back-compat single-terminal slice (now a thin wrapper)."""
    return _subpipeline_for_terminals(pipeline, [terminal])
```

Extend the `CompiledProcedure` dataclass (add two fields with defaults so existing constructions still work):

```python
@dataclass(frozen=True)
class CompiledProcedure:
    procedure_id: str
    title: str
    narrative: str
    result: CompileResult
    code: str = ""
    assertion: str = ""
```

- [ ] **Step 4: Rewrite `compile_pipeline_procedures` to key off procedures**

Replace the body of `compile_pipeline_procedures`:

```python
def compile_pipeline_procedures(pipeline: Pipeline) -> list[CompiledProcedure]:
    """Compile each **effective procedure** to a :class:`CompiledProcedure`.

    A procedure may own several Test nodes; its ``result`` is compiled from the
    union sub-pipeline of those tests (the existing multi-terminal ``_out_<id>``
    union emit produces one ``test()`` returning the concatenation of the checks'
    violations). Falls back to one-procedure-per-terminal when none are defined.
    """
    from uticen_lite.pipeline.procedures import (
        effective_procedures,
        tests_for_procedure,
    )

    out: list[CompiledProcedure] = []
    for proc in effective_procedures(pipeline):
        tests = tests_for_procedure(pipeline, proc.id)
        if not tests:
            continue
        sub = _subpipeline_for_terminals(pipeline, tests)
        title = proc.name or (tests[0].config.get("title") or f"Test {tests[0].id}")
        out.append(
            CompiledProcedure(
                procedure_id=proc.id,
                title=str(title),
                narrative=proc.narrative or tests[0].narrative,
                result=compile_pipeline(sub),
                code=proc.code,
                assertion=proc.assertion,
            )
        )
    return out
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `python -m pytest tests/pipeline/test_compile_procedures.py tests/pipeline -q`
Expected: PASS, including existing `tests/pipeline/test_compile.py` (single-terminal + forked cases still compile identically — a forked pipeline with no `procedures` derives one-per-terminal, matching prior output). Run `ruff`/`mypy` on `uticen_lite/pipeline`.

- [ ] **Step 6: Commit + push**

```bash
git add uticen_lite/pipeline/compile.py tests/pipeline/test_compile_procedures.py
git commit -m "feat(compile): compile per effective procedure over a terminal-set closure" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>" \
  -m "Claude-Session: https://claude.ai/code/session_013JpkfZUEVoutkGJAYHqrKJ"
git push -u origin HEAD
```

---

## Task 3: Run — per-check runs, merge-by-item-key, distinct-examined population

**Files:**
- Modify: `uticen_lite/store/run_service.py`
- Test: `tests/store/test_run_service_procedures.py` (create)

**Interfaces:**
- Consumes: `effective_procedures`, `tests_for_procedure` (Task 1); `_subpipeline_for`, `compile_pipeline`, `compile_pipeline_procedures` (Task 2); `materialize_steps` (`pipeline/materialize.py`); existing `run_control`, `ControlDef`, `RunRecord`, `Violation`, `Threshold`, `ProcedureSpec`, `Workpaper.assemble_procedures`.
- Produces (module-private helpers in `run_service.py`):
  - `_distinct_examined(node_frames: dict[str, Any], tests: list[Node]) -> int | None` — `|⋃ distinct item-keys of each test's input frame|`; `None` when frames unavailable.
  - `_merge_violations(per_check: list[tuple[str, list[Violation]]]) -> list[Violation]` — one Violation per item-key, `details["checks"]` = sorted check labels that flagged it, severity = max.
  - Updated `_run_multi_procedure(...)` iterating effective procedures, running each check, synthesizing the procedure `RunRecord` (distinct-examined population + merged violations) and a display `ProcedureSpec` (code/assertion/union test_code).

- [ ] **Step 1: Write the failing test**

Create `tests/store/test_run_service_procedures.py`. (Builds a real store with a frozen CSV source, defines a 2-check single procedure, runs it, asserts distinct-examined population + merged exceptions + which-checks annotation.)

```python
import pandas as pd
from pathlib import Path

from uticen_lite.store.migrations import migrate
from uticen_lite.store import repo
from uticen_lite.store.run_service import run_control_in_store
import sqlite3


def _seed(tmp_path: Path) -> sqlite3.Connection:
    # 4 manual JEs (+1 auto filtered out). A fails both checks; B fails 'no approval';
    # C fails 'preparer=approver'. Distinct manual JEs examined = 4 (A,B,C,E).
    df = pd.DataFrame([
        {"je_id": "A", "kind": "manual", "preparer": "approver", "approval": ""},
        {"je_id": "B", "kind": "manual", "preparer": "alice",    "approval": ""},
        {"je_id": "C", "kind": "manual", "preparer": "approver", "approval": "yes"},
        {"je_id": "E", "kind": "manual", "preparer": "bob",      "approval": "yes"},
        {"je_id": "D", "kind": "auto",   "preparer": "approver", "approval": ""},
    ])
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    df.to_csv(data_dir / "je.csv", index=False)

    conn = sqlite3.connect(":memory:")
    migrate(conn)
    repo.upsert_source(conn, id="je", title="Journal Entries", path="data/je.csv",
                       columns=[{"original_name": c, "display_name": c} for c in df.columns],
                       key_columns=["je_id"])
    pipeline = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "je"},
            {"id": "flt", "type": "filter", "inputs": ["imp"],
             "config": {"logic": "all", "conditions": [{"column": "kind", "op": "eq", "value": "manual"}]}},
            {"id": "t1", "type": "test", "inputs": ["flt"], "title": "preparer=approver",
             "config": {"logic": "all", "item_key_column": "je_id", "procedure_id": "p1",
                        "description_template": "Preparer equals approver on {je_id}",
                        "conditions": [{"column": "preparer", "op": "eq", "value": "approver"}]}},
            {"id": "t2", "type": "test", "inputs": ["flt"], "title": "no approval",
             "config": {"logic": "all", "item_key_column": "je_id", "procedure_id": "p1",
                        "description_template": "No approval on {je_id}",
                        "conditions": [{"column": "approval", "op": "is_empty"}]}},
        ],
        "procedures": [
            {"id": "p1", "code": "P1", "name": "Manual JE Review",
             "assertion": "Segregation of Duties", "failure_threshold_count": 0, "position": 0},
        ],
    }
    repo.upsert_control(conn, id="gl1", title="Journal Integrity", objective="o",
                        narrative="n", framework_refs={}, test_kind="pipeline",
                        pipeline=pipeline)
    repo.set_control_sources(conn, "gl1", ["je"])
    return conn


def test_procedure_rollup_distinct_examined_and_merged(tmp_path):
    conn = _seed(tmp_path)
    run = run_control_in_store(conn, tmp_path, "gl1", "2026-06-28T00:00:00Z")
    # Aggregate run is persisted; per-procedure run carries the audit numbers.
    proc_runs = repo.runs_for_control(conn, "gl1")
    p1 = [r for r in proc_runs if r["procedure_id"] == "p1"][0]
    assert p1["population_size"] == 4          # distinct manual JEs examined (A,B,C,E)
    assert p1["failed"] == 3                   # distinct flagged items A,B,C
    # A is flagged by BOTH checks → one merged exception carrying both labels.
    violations = repo.violations_for_run(conn, p1["run_id"])
    a = [v for v in violations if v["item_key"] == "A"][0]
    assert sorted(a["details"]["checks"]) == ["no approval", "preparer=approver"]
```

> NOTE TO IMPLEMENTER: confirm the exact repo accessors (`runs_for_control`, `violations_for_run`, `upsert_source`, `set_control_sources`, `upsert_control` kwargs) against `uticen_lite/store/repo.py` and adjust names/kwargs to match — the seeding shape above mirrors the columns but the helper names must match the real API. Keep the **assertions** unchanged (population 4, failed 3, A carries both checks).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/store/test_run_service_procedures.py -q`
Expected: FAIL — population is the trunk size (5) and `details["checks"]` is absent.

- [ ] **Step 3: Add the rollup helpers**

In `uticen_lite/store/run_service.py`, add near the other module-private helpers:

```python
def _distinct_examined(node_frames: dict[str, Any], tests: list[Node]) -> int | None:
    """|⋃ distinct item-keys across each test's *input* (evaluated) frame|.

    Returns None when frames are unavailable (degrade to the run's population).
    Falls back to the frame index when a test has no item_key_column."""
    if not node_frames:
        return None
    seen: set[str] = set()
    for t in tests:
        if not t.inputs:
            continue
        frame = node_frames.get(t.inputs[0])
        if frame is None:
            return None
        key_col = t.config.get("item_key_column")
        if key_col and key_col in getattr(frame, "columns", []):
            seen.update(str(v) for v in frame[key_col].tolist())
        else:
            seen.update(str(i) for i in frame.index.tolist())
    return len(seen)


def _merge_violations(per_check: list[tuple[str, list[Violation]]]) -> list[Violation]:
    """One Violation per item-key; ``details['checks']`` lists the labels that flagged
    it; severity = max. Sanitized JSON-native via Violation.from_raw (learning 0020)."""
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for label, vlist in per_check:
        for v in vlist:
            slot = by_key.get(v.item_key)
            if slot is None:
                slot = {
                    "item_key": v.item_key,
                    "description": v.description,
                    "severity": v.severity,
                    "details": dict(v.details),
                    "_checks": [],
                    "_sev_rank": _severity_rank(v.severity),
                }
                by_key[v.item_key] = slot
                order.append(v.item_key)
            slot["details"].update(v.details)
            if label and label not in slot["_checks"]:
                slot["_checks"].append(label)
            if _severity_rank(v.severity) > slot["_sev_rank"]:
                slot["_sev_rank"] = _severity_rank(v.severity)
                slot["severity"] = v.severity
                slot["description"] = v.description
    merged: list[Violation] = []
    for k in order:
        slot = by_key[k]
        details = dict(slot["details"])
        details["checks"] = sorted(slot["_checks"])
        merged.append(Violation.from_raw({
            "item_key": slot["item_key"],
            "description": slot["description"],
            "severity": slot["severity"],
            "details": details,
        }))
    return merged
```

Add a small severity-rank helper if one isn't already importable (mirror the renderer's ordering):

```python
def _severity_rank(sev: Any) -> int:
    return {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(
        getattr(sev, "value", str(sev)), 1
    )
```

(Confirm imports at the top of `run_service.py`: `from typing import Any`, `from uticen_lite.pipeline.model import Node`, `from uticen_lite.model.violation import Violation`, and add `from uticen_lite.pipeline.materialize import materialize_steps`.)

- [ ] **Step 4: Rewrite `_run_multi_procedure` to run per-check and synthesize per-procedure RunRecords**

Replace the procedure loop in `_run_multi_procedure` with the version below. Key changes: iterate `effective_procedures`; materialize node frames once for distinct-examined population; run each check's single-terminal subpipeline; merge; synthesize the procedure `RunRecord`; build the display `ProcedureSpec` from the union compile.

```python
    from uticen_lite.pipeline.compile import (
        compile_pipeline,
        compile_pipeline_procedures,
        _subpipeline_for,
    )
    from uticen_lite.pipeline.procedures import effective_procedures, tests_for_procedure

    pipeline = parse_pipeline(raw_pipeline)

    # Materialize node frames ONCE (full population) for distinct-examined populations.
    # Best-effort: degrade to None (→ run population) if a source is missing (0013).
    node_frames: dict[str, Any] = {}
    try:
        frames = _load_run_frames(root, control, sources, pipeline)   # {source_id: DataFrame}
        node_frames = materialize_steps(pipeline, frames)
    except Exception:  # noqa: BLE001 — population is best-effort; never block the run
        node_frames = {}

    union_by_pid = {cp.procedure_id: cp for cp in compile_pipeline_procedures(pipeline)}

    per_proc_runs: list[tuple[ProcedureSpec, RunRecord]] = []
    for proc in effective_procedures(pipeline):
        tests = tests_for_procedure(pipeline, proc.id)
        if not tests:
            continue

        # Run each check separately so we know which check flagged each item.
        per_check: list[tuple[str, list[Violation]]] = []
        last_run: RunRecord | None = None
        for t in tests:
            sub = _subpipeline_for(pipeline, t)
            compiled = compile_pipeline(sub)
            transient = ControlDef(
                id=control.id, title=proc.name or control.title, objective=control.objective,
                narrative=proc.narrative, framework_refs=control.framework_refs,
                risk=control.risk, sources=control.sources, test_path="",
                test_code=compiled.test_code if compiled.test_kind == "python" else None,
                rule_spec=compiled.rule_spec if compiled.test_kind == "rule" else None,
                threshold=control.threshold,
            )
            r = run_control(transient, sources, root, executed_at)
            label = t.title or t.config.get("title") or t.id
            per_check.append((str(label), list(r.violations)))
            last_run = r

        merged = _merge_violations(per_check)
        population = _distinct_examined(node_frames, tests)
        if population is None:
            population = last_run.population_size if last_run else 0

        proc_run = RunRecord(
            control_id=control.id, executed_at=executed_at,
            population_size=population, violations=merged,
            provenance=last_run.provenance if last_run else [],
        )
        proc_run.procedure_id = proc.id
        object.__setattr__(proc_run, "run_id", _procedure_run_id(proc_run, proc.id))
        repo.insert_run(conn, proc_run)

        union_cp = union_by_pid.get(proc.id)
        display_code = (
            union_cp.result.test_code if union_cp and union_cp.result.test_kind == "python"
            else resolve_test_code(ControlDef(
                id=control.id, title=proc.name, objective=control.objective,
                narrative=proc.narrative, framework_refs=control.framework_refs,
                risk=control.risk, sources=control.sources, test_path="",
                test_code=(union_cp.result.test_code if union_cp else None),
                rule_spec=(union_cp.result.rule_spec if union_cp else None),
                threshold=control.threshold,
            ))
        )
        proc_threshold = _per_procedure_threshold(
            {"failure_threshold_pct": proc.failure_threshold_pct,
             "failure_threshold_count": proc.failure_threshold_count},
            control.threshold,
        )
        per_proc_runs.append((
            ProcedureSpec(
                title=proc.name or display_code, narrative=proc.narrative,
                test_code=display_code, threshold=proc_threshold,
                code=proc.code, assertion=proc.assertion,
            ),
            proc_run,
        ))
```

Then keep the existing union-aggregate + `Workpaper.assemble_procedures` + evidence-write tail, but set the aggregate population to the control-level distinct-examined across **all** tests:

```python
    all_tests = [t for proc in effective_procedures(pipeline)
                 for t in tests_for_procedure(pipeline, proc.id)]
    agg_population = _distinct_examined(node_frames, all_tests)
    all_violations = _merge_violations(
        [("", [v for _, run in per_proc_runs for v in run.violations])]
    )
    union_run = per_proc_runs[0][1]
    if len(per_proc_runs) != 1 or agg_population is not None:
        union_run = RunRecord(
            control_id=control.id, executed_at=executed_at,
            population_size=(agg_population if agg_population is not None
                             else per_proc_runs[0][1].population_size),
            violations=all_violations,
            provenance=per_proc_runs[0][1].provenance,
        )
        union_run.procedure_id = ""
        repo.insert_run(conn, union_run)
```

Add the frame loader helper (loads each bound source's DataFrame via the same adapter `run_control` uses, so populations match the run):

```python
def _load_run_frames(root, control, sources, pipeline) -> dict[str, Any]:
    """{source_id: DataFrame} for the pipeline's Import sources, via the source adapter."""
    from uticen_lite.adapters import source_for  # adjust import to the real adapter entry
    frames: dict[str, Any] = {}
    for sid in pipeline.import_source_ids():
        binding = sources[sid]
        frames[sid] = source_for(binding, root).load().df
    return frames
```

> NOTE TO IMPLEMENTER: match `ControlDef(...)`, `run_control(...)`, `resolve_test_code(...)`, `_per_procedure_threshold(...)`, `_procedure_run_id(...)`, and the adapter import (`source_for`) to their real signatures (see `runner/execute.py` and the existing `_run_multi_procedure`). The previous code constructed the same transient `ControlDef` — reuse its exact field list. Do NOT change `RunRecord`'s computed `failed`/`passed`/`pass_rate` semantics.

- [ ] **Step 5: Run the new test + the whole store suite**

Run: `python -m pytest tests/store/test_run_service_procedures.py tests/store -q`
Expected: the new test PASSES; existing store tests still pass for **unfiltered** single-test controls. **Filtered** single-test controls now report post-filter populations — if any existing `tests/store` assertion hardcodes a pre-filter population for a filtered control, update it to the post-filter value and add a one-line comment `# Decision A: distinct-examined population`. (The big demo fan-out is Task 6.)

- [ ] **Step 6: ruff + mypy + commit + push**

```bash
python -m ruff check uticen_lite/store && python -m mypy uticen_lite/store
git add uticen_lite/store/run_service.py tests/store/test_run_service_procedures.py
git commit -m "feat(run): per-check procedure rollup with distinct-examined population" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>" \
  -m "Claude-Session: https://claude.ai/code/session_013JpkfZUEVoutkGJAYHqrKJ"
git push -u origin HEAD
```

---

## Task 4: Workpaper + bundle — additive `code`/`assertion`, per-check render

**Files:**
- Modify: `uticen_lite/model/workpaper.py`, `uticen_lite/render/html.py`, `uticen_lite/render/markdown.py`, `uticen_lite/bundle/assemble.py`, `uticen_lite/schema/bundle.schema.json`, `contract/bundle.schema.json` (if present)
- Test: `tests/render/test_procedure_render.py` (create), extend `tests/test_contract_export.py`, `tests/schema/test_bundle_schema.py`

**Interfaces:**
- Consumes: `ProcedureSpec(code, assertion, …)` (Task 3).
- Produces: `Procedure.code`, `Procedure.assertion`; `Procedure.to_dict()` emits both; schema `$defs/procedure` accepts both (optional).

- [ ] **Step 1: Write the failing tests**

Create `tests/render/test_procedure_render.py`:

```python
from uticen_lite.model.workpaper import Procedure, Workpaper, ProcedureSpec
from uticen_lite.model.run import RunRecord
from uticen_lite.model.violation import Violation


def _proc() -> Procedure:
    run = RunRecord(control_id="gl1", executed_at="t", population_size=650, violations=[
        Violation.from_raw({"item_key": "A", "description": "x", "severity": "high",
                            "details": {"checks": ["no approval", "preparer=approver"]}}),
    ])
    return Procedure(code="P1", title="Manual JE Review", assertion="Segregation of Duties",
                     narrative="we tested…", test_code="def test(pop): ...", result=run)


def test_to_dict_emits_code_and_assertion():
    d = _proc().to_dict()
    assert d["code"] == "P1"
    assert d["assertion"] == "Segregation of Duties"
    assert d["title"] == "Manual JE Review"


def test_html_renders_code_assertion_and_checks():
    from uticen_lite.render.html import render_html  # match the real entry point
    wp = Workpaper(control_id="gl1", title="t", objective="o", narrative="n",
                   framework_refs={}, procedures=[_proc(), _proc()], generated_at="t")
    html = render_html(wp)
    assert "P1" in html and "Segregation of Duties" in html
    assert "preparer=approver" in html  # which-check annotation surfaced
```

Extend `tests/schema/test_bundle_schema.py` with a positive case asserting a procedure carrying `code`/`assertion` validates and that the `required` list is unchanged (still `["title","narrative","test_code","result"]`).

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/render/test_procedure_render.py -q`
Expected: FAIL — `Procedure` has no `code`/`assertion`.

- [ ] **Step 3: Add fields to `ProcedureSpec` + `Procedure`**

In `uticen_lite/model/workpaper.py`, add `code: str = ""` and `assertion: str = ""` to **both** `ProcedureSpec` and `Procedure` (after `title`). Update `Procedure.to_dict()`:

```python
    def to_dict(self) -> dict[str, Any]:
        """Bundle-facing dict — threshold/determination excluded (0015); code/assertion additive."""
        return {
            "code": self.code,
            "title": self.title,
            "assertion": self.assertion,
            "narrative": self.narrative,
            "test_code": self.test_code,
            "result": self.result.to_dict(),
        }
```

Thread `code`/`assertion` in `Workpaper.assemble_procedures` (copy from each `ProcedureSpec` into the constructed `Procedure`).

- [ ] **Step 4: Add optional schema properties (both copies)**

In `uticen_lite/schema/bundle.schema.json` (and `contract/bundle.schema.json` if it exists), under `$defs.procedure.properties` add:

```json
"code": { "type": "string" },
"assertion": { "type": "string" }
```

Leave `required` unchanged (`["title","narrative","test_code","result"]`). `additionalProperties` is already `true`.

- [ ] **Step 5: Thread through `bundle/assemble.py`**

In `_build_workpaper` (multi-procedure branch), add `"code": pi["code"]` and `"assertion": pi["assertion"]` to each appended procedure dict, and have `assemble_bundle` carry `code`/`assertion` in `procedure_info_by_control` entries. (Single-procedure branch: emit `"code": ""`, `"assertion": ""` for shape consistency.)

- [ ] **Step 6: Render code · name, assertion subtitle, per-check table column**

In `render/html.py` `_emit_procedures`: change the heading to `f"<h3>{_e(proc.code)} · {_e(proc.title)} {badge}</h3>"`, emit `f'<p class="assert">Assertion: {_e(proc.assertion)}</p>'` when `proc.assertion`, and add a **"Failed check(s)"** column to the per-procedure violations table sourced from `v.details.get("checks")`. Mirror in `render/markdown.py` `_render_procedures` (a `### {code}: {title} — {STATUS}` heading, an `_Assertion:_` line, and a `Failed checks` column). Keep N≤1 byte-identical where `code`/`assertion` are empty (guard the new lines on truthiness).

- [ ] **Step 7: Run render + contract gates**

Run:
```bash
python -m pytest tests/render/test_procedure_render.py tests/schema tests/test_contract_export.py -q
```
Expected: PASS. Then full `python -m pytest -q` may still fail on demo population numbers — that's Task 6.

- [ ] **Step 8: ruff + mypy + commit + push**

```bash
python -m ruff check uticen_lite && python -m mypy uticen_lite
git add uticen_lite/model/workpaper.py uticen_lite/render uticen_lite/bundle/assemble.py uticen_lite/schema/bundle.schema.json contract/bundle.schema.json tests/render/test_procedure_render.py tests/schema/test_bundle_schema.py
git commit -m "feat(workpaper): additive code/assertion + per-check exception rendering" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>" \
  -m "Claude-Session: https://claude.ai/code/session_013JpkfZUEVoutkGJAYHqrKJ"
git push -u origin HEAD
```

---

## Task 5: Builder UX — Procedures panel, per-Test selector, derived colors

**Files:**
- Modify: `uticen_lite/plane/routes/pipeline.py` (`_editor_context`, `save_pipeline`, `_diagram`)
- Create: `uticen_lite/plane/templates/partials/_procedures_panel.html`
- Modify: `uticen_lite/plane/templates/partials/_pipe_node.html`, `logic_builder.html`, `partials/_pipe_diagram.html`, `logic_flowchart.html`
- Test: extend `tests/e2e/test_smoke.py`; add `tests/plane/test_procedures_panel.py`

**Interfaces:**
- Consumes: `effective_procedures`, `derived_membership` (Task 1).
- Produces: a stable 6-color palette helper `procedure_color(position: int) -> str`; template context keys `procedures`, `node_procedures` (`{node_id: [{"id","code","color"}]}`).

- [ ] **Step 1: Add the color palette + context (plane route)**

In `uticen_lite/plane/routes/pipeline.py`, add near the top:

```python
_PROC_PALETTE = ["#4f7cff", "#18a999", "#d9822b", "#9b5de5", "#e5556e", "#3aa0c2"]

def procedure_color(position: int) -> str:
    return _PROC_PALETTE[position % len(_PROC_PALETTE)]
```

In `_editor_context` (when `for_builder`), add to the returned ctx:

```python
    from uticen_lite.pipeline.procedures import effective_procedures, derived_membership
    try:
        _pipe = parse_pipeline(graph)            # the already-parsed builder graph
        eff = effective_procedures(_pipe)
        color_by_pid = {p.id: procedure_color(i) for i, p in enumerate(eff)}
        mem = derived_membership(_pipe)
        ctx["procedures"] = [
            {"id": p.id, "code": p.code or f"P{i+1}", "name": p.name,
             "assertion": p.assertion, "failure_threshold_pct": p.failure_threshold_pct,
             "failure_threshold_count": p.failure_threshold_count,
             "color": color_by_pid[p.id]}
            for i, p in enumerate(eff)
        ]
        ctx["node_procedures"] = {
            nid: [{"id": pid, "code": next((pp["code"] for pp in ctx["procedures"] if pp["id"] == pid), pid),
                   "color": color_by_pid.get(pid, "#888")}
                  for pid in sorted(pids)]
            for nid, pids in mem.items()
        }
    except Exception:  # noqa: BLE001 — incomplete graph → no panel data, never 500 (0013)
        ctx["procedures"] = []
        ctx["node_procedures"] = {}
```

- [ ] **Step 2: Create `_procedures_panel.html`**

`uticen_lite/plane/templates/partials/_procedures_panel.html`:

```html
<section class="proc-panel" data-proc-panel>
  <h3>Procedures</h3>
  <div id="proc-list">
    {% for p in procedures %}
    <div class="proc-row" data-proc-row data-proc-id="{{ p.id }}">
      <span class="proc-dot" style="background:{{ p.color }}"></span>
      <input data-proc-code   value="{{ p.code }}"      style="width:54px" aria-label="Code">
      <input data-proc-name   value="{{ p.name }}"      placeholder="Name" style="flex:1;min-width:160px">
      <input data-proc-assert value="{{ p.assertion }}" placeholder="Assertion / category" style="flex:1;min-width:160px">
      <input data-proc-pct    value="{{ p.failure_threshold_pct if p.failure_threshold_pct is not none else '' }}" placeholder="thr %" style="width:64px">
      <input data-proc-count  value="{{ p.failure_threshold_count if p.failure_threshold_count is not none else '' }}" placeholder="count" style="width:64px">
      <button type="button" data-proc-del aria-label="Remove">✕</button>
    </div>
    {% endfor %}
  </div>
  <button type="button" id="proc-add">＋ Add procedure</button>
</section>
```

Include it at the top of the Builder body in `logic_builder.html` (above `_pipe_cards.html`): `{% include "partials/_procedures_panel.html" %}`.

- [ ] **Step 3: Per-Test "Procedure ▾" selector + color chip in `_pipe_node.html`**

In the Test-terminal extras block of `partials/_pipe_node.html` (after the "Procedure title" row), add:

```html
<div class="pipe-row">
  <label>Procedure</label>
  <select data-procedure>
    <option value="">— unassigned —</option>
    {% for p in procedures %}
    <option value="{{ p.id }}" {% if node.config.get('procedure_id') == p.id %}selected{% endif %}>{{ p.code }} · {{ p.name }}</option>
    {% endfor %}
  </select>
  {% for chip in node_procedures.get(node.id, []) %}
  <span class="proc-chip" style="border-color:{{ chip.color }}">{{ chip.code }}</span>
  {% endfor %}
</div>
```

For **support** cards (Import/Filter/Join), render just the derived chips (read-only) in a small footer row:

```html
{% if node.type != 'test' and node_procedures.get(node.id) %}
<div class="pipe-row pipe-chips">
  {% for chip in node_procedures.get(node.id, []) %}
  <span class="proc-chip" style="border-color:{{ chip.color }}">{{ chip.code }}</span>
  {% endfor %}
</div>
{% endif %}
```

- [ ] **Step 4: Serialize procedures + per-Test `procedure_id` (logic_builder.html JS)**

In the `serialize()` function, inside the `if (type === 'test')` block add:

```javascript
    node.config.procedure_id = (card.querySelector('[data-procedure]') || {}).value || null;
```

Add a `serializeProcedures()` that reads the panel rows into `graph.procedures` and call it from both the submit handler and `autosaveSubmit`:

```javascript
function serializeProcedures() {
    var rows = document.querySelectorAll('[data-proc-row]');
    graph.procedures = Array.prototype.map.call(rows, function (row, i) {
        var pct = (row.querySelector('[data-proc-pct]') || {}).value;
        var cnt = (row.querySelector('[data-proc-count]') || {}).value;
        return {
            id: row.getAttribute('data-proc-id'),
            code: (row.querySelector('[data-proc-code]') || {}).value || ('P' + (i + 1)),
            name: (row.querySelector('[data-proc-name]') || {}).value || '',
            assertion: (row.querySelector('[data-proc-assert]') || {}).value || '',
            failure_threshold_pct: pct === '' ? null : Number(pct),
            failure_threshold_count: cnt === '' ? null : Number(cnt),
            position: i
        };
    });
}
```

Call `serializeProcedures();` right after `serialize();` in the submit listener and before building the autosave `FormData`. "Add procedure" generates a row with a fresh id (`'p_' + Math.random().toString(36).slice(2, 8)`).

- [ ] **Step 5: Accept procedures in `save_pipeline` (route)**

`save_pipeline` already JSON-parses `pipeline_json` into `graph`; `graph["procedures"]` now rides along and `parse_pipeline` (Task 1) accepts it. Confirm `_save_pipeline_graph` persists the **whole** graph dict (it serializes `graph` to the `pipeline` column) — if it reconstructs a nodes-only dict, fix it to preserve `procedures`. Add a `tests/plane/test_procedures_panel.py` asserting a POST with `procedures` round-trips through the store and back into the GET context.

- [ ] **Step 6: Per-procedure flowchart color + legend**

In `_diagram(pipeline, counts)`, compute `derived_membership` + `effective_procedures`, and add to each box dict `"proc_color"` = the color of its **first** owning procedure (or `None`). In `partials/_pipe_diagram.html`, apply it: `<rect ... style="{% if box.proc_color %}stroke:{{ box.proc_color }}{% endif %}" />`. Add a legend above the SVG listing `code · name` with each color. Add `.proc-chip`, `.proc-dot`, `.proc-panel` styles to `app.css` (route through `var(--token)` per learning 0005; qualify input rules with `[type=...]`/a second class so the panel inputs out-specify the base field block per learning 0032).

- [ ] **Step 7: Extend the e2e browser smoke (learning 0012)**

In `tests/e2e/test_smoke.py`, after the existing Test-node setup, add: click `#proc-add`, fill a procedure row (`[data-proc-code]`=`P1`, `[data-proc-name]`=`Manual JE Review`, `[data-proc-assert]`=`Segregation of Duties`), select it on the Test card (`[data-node="tst"] [data-procedure]` → the new procedure), Save, reload `/controls/sod/logic/builder`, and `expect` the panel row + the selected option to persist.

- [ ] **Step 8: Run plane + e2e, ruff, mypy, commit + push**

```bash
python -m pytest tests/plane/test_procedures_panel.py -q
python -m pytest tests/e2e -m browser -q   # requires: playwright install chromium
python -m ruff check uticen_lite/plane && python -m mypy uticen_lite/plane
git add uticen_lite/plane tests/plane/test_procedures_panel.py tests/e2e/test_smoke.py
git commit -m "feat(plane): Procedures panel, per-Test selector, derived colors" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>" \
  -m "Claude-Session: https://claude.ai/code/session_013JpkfZUEVoutkGJAYHqrKJ"
git push -u origin HEAD
```

---

## Task 6: Uniform-population fan-out (learning 0031) + docs

**Files:**
- Modify: `examples/northwind-trading/**` expected outputs/fixtures, any `tests/` count assertions, `README.md`, `PRODUCT-MAP.md`
- Test: the whole suite green

**Interfaces:** none new — this reconciles every hardcoded count/pass-rate to the new distinct-examined basis.

- [ ] **Step 1: Find every affected literal**

Run:
```bash
python -m pytest -q 2>&1 | tee /tmp/proc_fanout.txt   # see which assertions now fail
grep -rnE "population|pass_rate|records_tested|== [0-9]{2,}|[0-9]{3,}" tests examples README.md PRODUCT-MAP.md
```
List every failing assertion + the file/line. Expect hits in `tests/test_northwind*`, `tests/test_import_service*`, `tests/test_contract_export.py`, `tests/plane/test_wheel_build.py`, and any render snapshot fixtures (same fan-out shape as learnings 0031/0004/0014).

- [ ] **Step 2: Recompute + update each expected value**

For each filtered pipeline control in the Northwind demo, the population becomes the post-filter distinct-examined count. Run the demo to get authoritative numbers:
```bash
python -m uticen_lite.cli run examples/northwind-trading   # or: uticen-lite run examples/northwind-trading
```
Update each fixture/assertion to the printed values; add `# Decision A: distinct-examined population` next to changed literals. Keep the public-API-sourced demo source a **frozen CSV** so CI stays offline/deterministic (learning 0025).

- [ ] **Step 3: Update prose**

Update `README.md` (any cited demo numbers) and `PRODUCT-MAP.md` — extend the **Logic** row + **Workpaper renderer** row to describe author-defined procedures (grouping layer, per-procedure code/assertion/threshold, distinct-examined population, derived membership/colors) and the **examples** row if a control becomes multi-procedure (Task 7).

- [ ] **Step 4: Whole-suite green + gates**

Run:
```bash
python -m pytest -q
python -m ruff check . && python -m mypy uticen_lite
```
Expected: all green, output pristine (no stray warnings).

- [ ] **Step 5: Commit + push**

```bash
git add -A
git commit -m "test(demo): reconcile counts to distinct-examined population + doc procedures" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>" \
  -m "Claude-Session: https://claude.ai/code/session_013JpkfZUEVoutkGJAYHqrKJ"
git push -u origin HEAD
```

---

## Task 7 (optional follow-up): Northwind multi-procedure showcase

**Files:** one `examples/northwind-trading/controls/<gl-control>/pipeline.yaml` (add a second check + a `procedures` block), plus the fan-out (Task 6 re-run).

- [ ] **Step 1:** Pick the journal-entry/GL control; add a second Test branch and a `procedures: [{id, code: P1, name, assertion: "Segregation of Duties", …}]` block grouping both checks under P1 (and a P2 if a second assertion fits).
- [ ] **Step 2:** Re-run the demo, update fixtures/README/PRODUCT-MAP, full suite green.
- [ ] **Step 3:** Commit + push (same trailer + `git push -u origin HEAD`).

---

## Self-Review (run after writing; performed)

**Spec coverage:** every spec section maps to a task — model+membership (T1), compile (T2), run/rollup/distinct-examined (T3), workpaper+bundle additive (T4), Builder UX+flowchart colors (T5), uniform fan-out + docs (T6), optional showcase (T7). Edge cases (unassigned test, missing item-key, incomplete graph) are covered in T1/T3/T5 with explicit degrade paths. Testing strategy (equivalence 0009, distinct-items math, dedup/merge, back-compat, contract teeth, e2e) is distributed across T1–T6.

**Placeholders:** none of the banned patterns; the two "NOTE TO IMPLEMENTER" blocks point at exact existing symbols to match (not vague "handle errors") and the asserted behavior is fully specified.

**Type consistency:** `ProcedureDef`/`CompiledProcedure`/`ProcedureSpec`/`Procedure` field names (`code`, `assertion`, `name`/`title`, `procedure_id`, `failure_threshold_pct/_count`, `position`) are used consistently across T1→T5; `_distinct_examined`/`_merge_violations`/`procedure_color` signatures match their call sites; `details["checks"]` is the single annotation key used by both T3 (write) and T4 (render).
