# Single-record trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `Logic ▸ Trace` sub-tab that walks one record (by item key) through a control's pipeline — present/dropped at each node, then per-condition pass/fail at each Test — so an author sees exactly where a record is flagged or why it passes.

**Architecture:** A pure, runner-side helper `trace_record(pipeline, frames, key, sources)` builds a view-model from the already-cached materialized step frames (`_materialize_full`) and the canonical per-condition evaluator (`_condition_mask`). A new read-only GET route renders it through a new template. Render-only: no store write, no bundle change.

**Tech Stack:** Python ≥3.11, pandas (runner-side only), FastAPI + Jinja2 + HTMX-free plain GET, pytest, Playwright (e2e).

## EXECUTION RULES

- Never ask the user for permission to continue between tasks.
- Execute the full plan start to finish without interruption.
- On an unresolvable error after 2–3 attempts: note it and skip to the next task.
- Push after every commit (each task's commit step includes `git push -u origin HEAD`).

## Global Constraints

- Python floor **≥3.11**; ruff target `py311`, line-length **100**.
- Dev gates must stay green at every commit: `python -m pytest -q` (pristine — no stray warnings), `python -m ruff check .`, `python -m mypy uticen_lite`.
- **Render-only:** never touch `contract/bundle.schema.json`, the bundle `schema_version`, or the store schema. The trace is ephemeral (learnings 0001, 0015).
- **Never 500:** every new route body is wrapped so any failure degrades to a friendly message (learnings 0013, 0033).
- **Pyodide boundary:** `trace.py` is runner-side (it imports pandas, like `materialize.py`); it must NOT import from `uticen_lite.plane.*` (no web→core inversion).

---

## File Structure

- `uticen_lite/pipeline/trace.py` — **new.** Pure helper `trace_record(...)` + its view-model dataclasses. Runner-side (imports pandas via `_condition_mask`/`Population`). One responsibility: turn a (pipeline, frames, key, sources) tuple into a `TraceResult`.
- `uticen_lite/plane/routes/pipeline.py` — **modify.** Add `_load_source_populations(...)`, refactor `_load_full_frames` to delegate to it, add the `logic_trace` GET route, add `import logging` + module `logger`.
- `uticen_lite/plane/templates/logic_trace.html` — **new.** The Trace tab page (picker + example chips + walk + per-Test verdict/conditions).
- `uticen_lite/plane/templates/partials/_logic_tabs.html` — **modify.** Add the `Trace` tab link.
- `tests/pipeline/test_trace.py` — **new.** Unit tests for `trace_record`.
- `tests/plane/test_logic_trace.py` — **new.** Route tests (rendered HTML, degradation, never-500).
- `tests/e2e/test_record_trace_smoke.py` — **new.** Browser smoke (learning 0012).

---

## Task 1: `trace_record` core helper + view-model

**Files:**
- Create: `uticen_lite/pipeline/trace.py`
- Test: `tests/pipeline/test_trace.py`

**Interfaces:**
- Consumes: `uticen_lite.pipeline.model.Pipeline`/`Node` (`.nodes`, `.terminals`, `.topological()`, node `.type`/`.config`/`.inputs`/`.source_id`); `uticen_lite.model.population.Population` (`.df`, `.key_columns`); `uticen_lite.rules.evaluate._condition_mask`; `uticen_lite.rules.spec.parse_rule_spec`/`RuleSpecError`.
- Produces (relied on by Task 2's route + template):
  - `trace_record(pipeline: Pipeline, frames: dict[str, Any], key: str, sources: dict[str, Population]) -> TraceResult`
  - `TraceResult(key, key_column: str|None, source_id: str|None, found: bool, shared_count: int, steps: list[NodeStep], tests: list[TestStep], message: str)`
  - `NodeStep(id, label, type, status: str, reason: str)` — `status` ∈ `present|dropped|absent|indeterminate`
  - `TestStep(id, label, reached: bool|None, flagged: bool|None, logic: str, conditions: list[ConditionRow], note: str)`
  - `ConditionRow(column, op, value, actual, passed: bool|None, note: str)`

- [ ] **Step 1: Write the failing tests**

Create `tests/pipeline/test_trace.py`:

```python
"""Unit tests for the single-record trace view-model (issue #29)."""
from __future__ import annotations

import pandas as pd

from uticen_lite.model.population import ColumnMeta, Population
from uticen_lite.pipeline.model import parse_pipeline
from uticen_lite.pipeline.trace import trace_record


def _pop(df: pd.DataFrame, key: str = "id", sid: str = "src") -> Population:
    cols = [
        ColumnMeta(original_name=c, display_name=c, is_key=(c == key))
        for c in df.columns
    ]
    return Population(df=df, columns=cols, source_id=sid)


def _simple_pipeline():
    # Import(src) → Test(amount > 100)
    return parse_pipeline({"nodes": [
        {"id": "imp", "type": "import", "source_id": "src", "inputs": []},
        {"id": "tst", "type": "test", "inputs": ["imp"], "config": {
            "logic": "all",
            "conditions": [{"column": "amount", "op": "gt", "value": 100}],
        }},
    ]})


def test_flagged_record_shows_condition_true_and_flagged():
    df = pd.DataFrame({"id": ["A", "B"], "amount": [150, 50]})
    frames = {"imp": df, "tst": df[df["amount"] > 100]}
    res = trace_record(_simple_pipeline(), frames, "A", {"src": _pop(df)})
    assert res.found is True
    assert res.key_column == "id"
    assert res.tests[0].flagged is True
    cond = res.tests[0].conditions[0]
    assert cond.column == "amount" and cond.passed is True
    assert str(cond.actual) == "150"


def test_passing_record_not_flagged():
    df = pd.DataFrame({"id": ["A", "B"], "amount": [150, 50]})
    frames = {"imp": df, "tst": df[df["amount"] > 100]}
    res = trace_record(_simple_pipeline(), frames, "B", {"src": _pop(df)})
    assert res.tests[0].flagged is False
    assert res.tests[0].conditions[0].passed is False


def test_record_dropped_at_filter_is_reported_at_that_node():
    pipe = parse_pipeline({"nodes": [
        {"id": "imp", "type": "import", "source_id": "src", "inputs": []},
        {"id": "flt", "type": "filter", "inputs": ["imp"], "config": {
            "logic": "all",
            "conditions": [{"column": "active", "op": "eq", "value": "Y"}],
        }},
        {"id": "tst", "type": "test", "inputs": ["flt"], "config": {
            "logic": "all",
            "conditions": [{"column": "amount", "op": "gt", "value": 100}],
        }},
    ]})
    df = pd.DataFrame({"id": ["A", "B"], "active": ["N", "Y"], "amount": [150, 150]})
    filtered = df[df["active"] == "Y"]
    frames = {"imp": df, "flt": filtered, "tst": filtered[filtered["amount"] > 100]}
    res = trace_record(pipe, frames, "A", {"src": _pop(df)})
    flt_step = next(s for s in res.steps if s.id == "flt")
    assert flt_step.status == "dropped"
    assert res.tests[0].reached is False


def test_missing_key_reports_not_found():
    df = pd.DataFrame({"id": ["A"], "amount": [1]})
    frames = {"imp": df, "tst": df.iloc[0:0]}
    res = trace_record(_simple_pipeline(), frames, "ZZZ", {"src": _pop(df)})
    assert res.found is False
    assert "No record" in res.message


def test_non_unique_key_traces_first_and_counts():
    df = pd.DataFrame({"id": ["A", "A"], "amount": [150, 50]})
    frames = {"imp": df, "tst": df[df["amount"] > 100]}
    res = trace_record(_simple_pipeline(), frames, "A", {"src": _pop(df)})
    assert res.found is True
    assert res.shared_count == 2


def test_exists_in_condition_uses_other_source():
    main = pd.DataFrame({"id": ["A", "B"], "vendor": ["v1", "v9"]})
    other = pd.DataFrame({"vendor_id": ["v1"]})
    pipe = parse_pipeline({"nodes": [
        {"id": "imp", "type": "import", "source_id": "src", "inputs": []},
        {"id": "tst", "type": "test", "inputs": ["imp"], "config": {
            "logic": "all",
            "conditions": [{"op": "exists_in", "other_source": "vendors",
                            "this_key": "vendor", "other_key": "vendor_id"}],
        }},
    ]})
    out = main[main["vendor"].isin({"v1"})]
    frames = {"imp": main, "tst": out}
    sources = {
        "src": _pop(main, key="id"),
        "vendors": _pop(other, key="vendor_id", sid="vendors"),
    }
    res = trace_record(pipe, frames, "A", sources)
    cond = res.tests[0].conditions[0]
    assert cond.passed is True
    assert "found in vendors" in cond.note


def test_custom_python_terminal_has_no_condition_detail():
    pipe = parse_pipeline({"nodes": [
        {"id": "imp", "type": "import", "source_id": "src", "inputs": []},
        {"id": "cpy", "type": "custom_python", "inputs": ["imp"], "config": {
            "flavor": "test", "code": "rows = rows"}},
    ]})
    df = pd.DataFrame({"id": ["A"], "amount": [1]})
    frames = {"imp": df, "cpy": pd.DataFrame({"item_key": ["A"]})}
    res = trace_record(pipe, frames, "A", {"src": _pop(df)})
    assert res.tests[0].conditions == []
    assert "custom Python" in res.tests[0].note


def test_source_without_key_column_degrades():
    df = pd.DataFrame({"amount": [1]})
    pop = Population(df=df, columns=[ColumnMeta("amount", "amount")], source_id="src")
    frames = {"imp": df, "tst": df.iloc[0:0]}
    res = trace_record(_simple_pipeline(), frames, "A", {"src": pop})
    assert res.key_column is None
    assert "no key column" in res.message.lower()


def test_condition_on_missing_column_does_not_crash():
    df = pd.DataFrame({"id": ["A"], "amount": [150]})
    pipe = parse_pipeline({"nodes": [
        {"id": "imp", "type": "import", "source_id": "src", "inputs": []},
        {"id": "tst", "type": "test", "inputs": ["imp"], "config": {
            "logic": "all",
            "conditions": [{"column": "nope", "op": "gt", "value": 1}],
        }},
    ]})
    frames = {"imp": df, "tst": df.iloc[0:0]}
    res = trace_record(pipe, frames, "A", {"src": _pop(df)})
    # The bad condition is reported as un-evaluatable, not a crash.
    assert res.tests[0].conditions[0].passed is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/pipeline/test_trace.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'uticen_lite.pipeline.trace'`

- [ ] **Step 3: Implement `trace.py`**

Create `uticen_lite/pipeline/trace.py`:

```python
"""Walk one record through a control pipeline → a render-only trace view-model.

Given the already-materialised per-node frames (``_materialize_full``) plus the
bound source populations, follow a single record (by item key) through every
non-terminal node — present / dropped / indeterminate — then show per-condition
pass/fail at each terminal Test. The same object is a debugging tool (where did
my record drop out?) and the audit-narrative sentence for a flagged record
(issue #29).

Runner-side, like :mod:`uticen_lite.pipeline.materialize`: it imports pandas via
``_condition_mask`` and never imports from ``uticen_lite.plane`` (no web→core
inversion). Per-condition verdicts reuse the canonical ``_condition_mask`` so the
trace agrees with the real run byte-for-byte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from uticen_lite.model.population import Population
from uticen_lite.pipeline.model import Node, Pipeline
from uticen_lite.rules.evaluate import _condition_mask
from uticen_lite.rules.spec import RuleSpec, RuleSpecError, parse_rule_spec


@dataclass
class ConditionRow:
    column: str
    op: str
    value: Any
    actual: Any
    passed: bool | None  # None → couldn't evaluate (bad column/op/source)
    note: str = ""


@dataclass
class NodeStep:
    id: str
    label: str
    type: str
    status: str  # "present" | "dropped" | "absent" | "indeterminate"
    reason: str = ""


@dataclass
class TestStep:
    id: str
    label: str
    reached: bool | None  # did the record arrive at this Test's input?
    flagged: bool | None  # is the record in this Test's violations?
    logic: str
    conditions: list[ConditionRow] = field(default_factory=list)
    note: str = ""


@dataclass
class TraceResult:
    key: str
    key_column: str | None
    source_id: str | None
    found: bool
    shared_count: int
    steps: list[NodeStep] = field(default_factory=list)
    tests: list[TestStep] = field(default_factory=list)
    message: str = ""  # set when the trace can't run (degraded / not found)


_DROP_REASON = {
    "import": "Not present in the imported source.",
    "filter": "A Filter condition excluded this record.",
    "join": "The Join found no match for this record.",
    "custom_python": "A custom Python step removed this record.",
}


def _node_label(node: Node) -> str:
    """A short human label for a node (mirrors the builder's labels).

    Duplicated here deliberately: this module is runner-side and must not import
    from ``uticen_lite.plane.routes.pipeline`` (web→core inversion). The node
    vocabulary is stable.
    """
    if node.title:
        return node.title
    if node.type == "import":
        return f"Import · {node.source_id}"
    if node.type == "filter":
        n = len(node.config.get("conditions", []))
        return f"Filter · {n} condition{'s' if n != 1 else ''}"
    if node.type == "join":
        return f"Join · {node.config.get('mode', '?')}"
    if node.type == "custom_python":
        return f"Custom Python · {node.config.get('flavor', '?')}"
    if node.type == "test":
        return "Test"
    return node.type


def _display(value: Any) -> Any:
    """NaN/NaT-safe display value."""
    import pandas as pd
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return value


def _present(frame: Any, key_column: str | None, key: str) -> bool | None:
    """True/False if *key* is/ isn't in *frame*; None if it can't be determined."""
    if frame is None or key_column is None or key_column not in getattr(frame, "columns", []):
        return None
    return bool((frame[key_column].astype(str) == str(key)).any())


def _xsrc_note(cond: Any, passed: bool) -> str:
    if cond.op == "exists_in":
        return f"{'found' if passed else 'not found'} in {cond.other_source}"
    if cond.op == "not_exists_in":
        return f"{'not found' if passed else 'found'} in {cond.other_source}"
    return ""


def _condition_rows(
    input_frame: Any, key_column: str, key: str,
    spec: RuleSpec, sources: dict[str, Population],
) -> list[ConditionRow]:
    """Per-condition pass/fail for the matched row in *input_frame*.

    Masks are evaluated over the WHOLE input frame (``is_duplicate``/``exists_in``
    are population-relative), then read positionally at the first matching row —
    dup-index safe.
    """
    flags = (input_frame[key_column].astype(str) == str(key)).to_numpy()
    rows: list[ConditionRow] = []
    if not flags.any():
        return rows
    pos = int(flags.argmax())
    for cond in spec.conditions:
        actual = (
            _display(input_frame.iloc[pos][cond.column])
            if cond.column in input_frame.columns else ""
        )
        try:
            mask = _condition_mask(input_frame, cond, sources)
            passed: bool | None = bool(mask.iloc[pos])
            note = _xsrc_note(cond, passed)
        except Exception as exc:  # noqa: BLE001 — bad column/op/source must not crash the trace
            passed, note = None, f"couldn't evaluate: {exc}"
        rows.append(ConditionRow(cond.column, cond.op, cond.value, actual, passed, note))
    return rows


def trace_record(
    pipeline: Pipeline,
    frames: dict[str, Any],
    key: str,
    sources: dict[str, Population],
) -> TraceResult:
    """Build the trace view-model for *key* over the materialised *frames*."""
    import_nodes = [n for n in pipeline.nodes if n.type == "import"]
    first_import = import_nodes[0] if import_nodes else None
    source_id = first_import.source_id if first_import else None
    primary = sources.get(source_id) if source_id else None
    key_column = primary.key_columns[0] if primary and primary.key_columns else None

    result = TraceResult(
        key=key, key_column=key_column, source_id=source_id,
        found=False, shared_count=0,
    )

    if key_column is None:
        result.message = (
            "This control's source has no key column, so a record can't be "
            "traced by key."
        )
        return result

    import_frame = frames.get(first_import.id) if first_import else None
    if import_frame is None:
        result.message = "Bind a data source to trace a record."
        return result

    flags = (import_frame[key_column].astype(str) == str(key)).to_numpy()
    result.shared_count = int(flags.sum())
    result.found = bool(flags.any())
    if not result.found:
        result.message = f"No record with {key_column} = {key!r} in this source."
        return result

    terminal_ids = {t.id for t in pipeline.terminals}

    # ── non-terminal walk: present / dropped / indeterminate ──────────────
    dropped = False
    for node in pipeline.topological():
        if node.id in terminal_ids:
            continue
        frame = frames.get(node.id)
        present = _present(frame, key_column, key)
        if present is None:
            status = "indeterminate"
            reason = (
                "Not computed yet." if frame is None
                else "The key column isn't carried past this step."
            )
        elif present:
            status, reason = "present", ""
        elif not dropped:
            status, reason = "dropped", _DROP_REASON.get(node.type, "Excluded here.")
            dropped = True
        else:
            status, reason = "absent", "Removed at an earlier step."
        result.steps.append(NodeStep(node.id, _node_label(node), node.type, status, reason))

    # ── per-terminal Test detail ──────────────────────────────────────────
    for t in pipeline.terminals:
        input_frame = frames.get(t.inputs[0]) if t.inputs else None
        reached = _present(input_frame, key_column, key)
        flagged = _present(frames.get(t.id), key_column, key)
        logic = str(t.config.get("logic", "all"))
        step = TestStep(
            id=t.id, label=_node_label(t), reached=reached, flagged=flagged, logic=logic,
        )
        if t.type != "test":
            step.note = "This Test is authored in custom Python — no per-condition detail."
            result.tests.append(step)
            continue
        if reached is None:
            step.note = (
                "Couldn't locate this record at the Test input "
                "(the key column isn't carried here)."
            )
            result.tests.append(step)
            continue
        if not reached:
            step.note = "This record didn't reach the Test."
            result.tests.append(step)
            continue
        try:
            spec = parse_rule_spec(t.config)
        except RuleSpecError as exc:
            step.note = f"Couldn't read this Test's conditions: {exc}"
            result.tests.append(step)
            continue
        step.conditions = _condition_rows(input_frame, key_column, key, spec, sources)
        if step.flagged is None and step.conditions:
            passes = [c.passed for c in step.conditions if c.passed is not None]
            if passes:
                step.flagged = all(passes) if logic == "all" else any(passes)
        result.tests.append(step)

    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/pipeline/test_trace.py -q`
Expected: PASS (10 passed)

- [ ] **Step 5: Run the dev gates**

Run: `python -m ruff check . && python -m mypy uticen_lite && python -m pytest -q`
Expected: all green, no new warnings.

- [ ] **Step 6: Commit and push**

```bash
git add uticen_lite/pipeline/trace.py tests/pipeline/test_trace.py
git commit -m "feat(trace): pure single-record trace view-model (#29)"
git push -u origin HEAD
```

---

## Task 2: Trace route, source-population loader, nav tab, and template

**Files:**
- Modify: `uticen_lite/plane/routes/pipeline.py` (add `import logging` + `logger`; add `_load_source_populations`; refactor `_load_full_frames`; add `logic_trace` route)
- Modify: `uticen_lite/plane/templates/partials/_logic_tabs.html`
- Create: `uticen_lite/plane/templates/logic_trace.html`
- Test: `tests/plane/test_logic_trace.py`

**Interfaces:**
- Consumes: `trace_record`/`TraceResult` from Task 1; existing `_pipeline_for_view`, `_materialize_full`, `repo`, `is_raw_python`, `connect`.
- Produces: `GET /controls/{control_id}/logic/trace?key=<value>` → 200 HTML always (never 500).

- [ ] **Step 1: Write the failing route tests**

Create `tests/plane/test_logic_trace.py`:

```python
"""Route tests for the Logic ▸ Trace single-record trace (issue #29)."""
from __future__ import annotations

import io
import json

from uticen_lite.store import repo


def _make_source(client, sid, csv_bytes: bytes) -> None:
    client.post(
        "/sources",
        data={"source_id": sid, "format": "csv"},
        files={"file": (f"{sid}.csv", io.BytesIO(csv_bytes), "text/csv")},
        follow_redirects=False,
    )


def _conn(client):
    from uticen_lite.store.db import connect
    return connect(client.app.state.project_root)


def _set_key(client, sid, key_col) -> None:
    conn = _conn(client)
    try:
        src = repo.get_source(conn, sid)
        cols = [{**c, "is_key": (c["original_name"] == key_col)} for c in src["columns"]]
        repo.set_columns(conn, sid, cols)
    finally:
        conn.close()


def _make_control(client, cid="C1") -> None:
    client.post("/controls", data={
        "id": cid, "title": "Trace Test", "objective": "o", "narrative": "n",
    }, follow_redirects=False)


def _save_pipeline(client, cid, graph):
    return client.post(f"/controls/{cid}/logic/builder",
                       data={"pipeline_json": json.dumps(graph)},
                       follow_redirects=False)


_INVOICES = (
    b"invoice_id,amount\n"
    b"INV001,100\nINV002,200\nINV003,300\nINV004,400\nINV005,500\n"
)


def _seeded(client, conditions=None):
    _make_source(client, "invoices", _INVOICES)
    _set_key(client, "invoices", "invoice_id")
    cid = "TR1"
    _make_control(client, cid)
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "invoices", "inputs": []},
        {"id": "tst", "type": "test", "inputs": ["imp"], "config": {
            "logic": "all",
            "conditions": conditions if conditions is not None
            else [{"column": "amount", "op": "gt", "value": 100}],
        }},
    ]}
    _save_pipeline(client, cid, graph)
    return client, cid


def test_trace_tab_is_linked_on_builder(client):
    c, cid = _seeded(client)
    r = c.get(f"/controls/{cid}/logic/builder")
    assert r.status_code == 200
    assert f"/controls/{cid}/logic/trace" in r.text


def test_trace_picker_shows_example_keys(client):
    c, cid = _seeded(client)
    r = c.get(f"/controls/{cid}/logic/trace")
    assert r.status_code == 200
    assert "INV001" in r.text  # an example-key chip


def test_flagged_record_renders_flagged_and_condition(client):
    c, cid = _seeded(client)
    r = c.get(f"/controls/{cid}/logic/trace", params={"key": "INV005"})
    assert r.status_code == 200
    assert "Flagged as an exception" in r.text
    assert "amount" in r.text and "gt" in r.text


def test_passing_record_renders_passed(client):
    c, cid = _seeded(client)
    r = c.get(f"/controls/{cid}/logic/trace", params={"key": "INV001"})
    assert r.status_code == 200
    assert "Passed" in r.text


def test_missing_key_renders_not_found(client):
    c, cid = _seeded(client)
    r = c.get(f"/controls/{cid}/logic/trace", params={"key": "ZZZ"})
    assert r.status_code == 200
    assert "No record" in r.text


def test_python_control_degrades(client):
    _make_source(client, "invoices", _INVOICES)
    _set_key(client, "invoices", "invoice_id")
    cid = "PYC"
    _make_control(client, cid)
    c = client
    # Bind the source, then author raw Python via the python tab.
    _save_pipeline(c, cid, {"nodes": [
        {"id": "imp", "type": "import", "source_id": "invoices", "inputs": []},
        {"id": "tst", "type": "test", "inputs": ["imp"],
         "config": {"logic": "all", "conditions": []}},
    ]})
    c.post(f"/controls/{cid}/logic/convert", follow_redirects=False)
    r = c.get(f"/controls/{cid}/logic/trace", params={"key": "INV001"})
    assert r.status_code == 200
    assert "rule builder" in r.text


def test_bad_condition_column_never_500s(client):
    c, cid = _seeded(client, conditions=[{"column": "nope", "op": "gt", "value": 1}])
    r = c.get(f"/controls/{cid}/logic/trace", params={"key": "INV001"})
    assert r.status_code == 200  # never 500 (learnings 0013/0033)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/plane/test_logic_trace.py -q`
Expected: FAIL (404 / template not found — the route and template don't exist yet).

- [ ] **Step 3: Add the source-population loader + refactor `_load_full_frames`**

In `uticen_lite/plane/routes/pipeline.py`, add the `Population` import near the other model imports at the top of the file:

```python
from uticen_lite.model.population import Population
```

Add `import logging` to the stdlib import block and, just after the `_STEP_CACHE = _new_step_cache()` line (~line 82), add:

```python
logger = logging.getLogger(__name__)
```

Then **replace** the existing `_load_full_frames` function body with a delegating version and add `_load_source_populations` directly above it:

```python
def _load_source_populations(
    conn: sqlite3.Connection, root: Any, source_ids: list[str]
) -> dict[str, Population]:
    """Load the full :class:`Population` for each bound source (by source_id).

    Like :func:`_load_full_frames` but keeps the Population (its ``.df`` AND key
    columns), which the record trace needs to resolve the key column and to
    evaluate ``exists_in`` conditions. Returns only sources whose file exists; a
    broken/adapters-missing file is skipped.
    """
    from uticen_lite.adapters.files import source_for
    from uticen_lite.store.loader import _binding

    out: dict[str, Population] = {}
    for sid in source_ids:
        src = repo.get_source(conn, sid)
        current = repo.get_current_file(conn, sid)
        if not src or not current:
            continue
        fpath = root / current["stored_path"]
        if not fpath.is_file():
            continue
        try:
            out[sid] = source_for(_binding(src), root).load()
        except Exception:  # noqa: BLE001 — a broken/adapters-missing file yields no population
            continue
    return out


def _load_full_frames(
    conn: sqlite3.Connection, root: Any, source_ids: list[str]
) -> dict[str, Any]:
    """Load the FULL coerced DataFrame for each bound source (by source_id).

    Thin wrapper over :func:`_load_source_populations` (keeps the live badges /
    inspector unchanged) — the trace needs the Population, the badges only the df.
    """
    return {
        sid: pop.df
        for sid, pop in _load_source_populations(conn, root, source_ids).items()
    }
```

- [ ] **Step 4: Add the `logic_trace` route**

In `uticen_lite/plane/routes/pipeline.py`, inside `register(...)`, add this route next to the other `/logic/*` GETs (e.g. just after `logic_builder`). It MUST be registered before the `/controls/{control_id}` catch-all — it already is, because `controls.register()` runs after `pipeline.register()` (existing comment at the top of `register`).

```python
    @app.get("/controls/{control_id}/logic/trace", response_class=HTMLResponse)
    def logic_trace(
        control_id: str,
        request: Request,
        key: str = "",
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        from uticen_lite.pipeline.trace import trace_record

        root = request.app.state.project_root
        control = repo.get_control(conn, control_id)
        ctx: dict[str, Any] = {
            "project": repo.get_project(conn) or {"name": ""},
            "control": control,
            "control_id": control_id,
            "active": "logic",
            "logic_tab": "trace",
            "key": key,
            "examples": [],
            "trace": None,
            "message": "",
        }
        # The Trace tab must never 500: any failure degrades to a friendly page
        # (learnings 0013/0033).
        try:
            if control is None:
                ctx["message"] = "Control not found."
                return templates.TemplateResponse(request, "logic_trace.html", ctx)
            if is_raw_python(control):
                ctx["message"] = (
                    "Tracing needs the rule builder — this control is authored in Python."
                )
                return templates.TemplateResponse(request, "logic_trace.html", ctx)
            pipeline = _pipeline_for_view(control)
            if pipeline is None:
                ctx["message"] = (
                    "This control isn't ready to trace yet — add logic in the Builder first."
                )
                return templates.TemplateResponse(request, "logic_trace.html", ctx)
            sources = _load_source_populations(conn, root, pipeline.import_source_ids())
            import_ids = pipeline.import_source_ids()
            primary = sources.get(import_ids[0]) if import_ids else None
            if primary is not None and primary.key_columns:
                kc = primary.key_columns[0]
                ctx["examples"] = (
                    primary.df[kc].astype(str).drop_duplicates().head(5).tolist()
                )
            if not sources:
                ctx["message"] = "Bind a data source to trace a record."
                return templates.TemplateResponse(request, "logic_trace.html", ctx)
            if key:
                frames = _materialize_full(conn, root, pipeline)
                ctx["trace"] = trace_record(pipeline, frames, key, sources)
        except Exception:  # noqa: BLE001 — the Trace tab must never 500
            logger.exception("Unexpected error tracing record in control %r", control_id)
            ctx["trace"] = None
            ctx["message"] = "This control can't be traced right now."
        return templates.TemplateResponse(request, "logic_trace.html", ctx)
```

- [ ] **Step 5: Add the Trace tab to the nav**

In `uticen_lite/plane/templates/partials/_logic_tabs.html`, add the Trace link after the Flowchart tab:

```html
  <a href="/controls/{{ control.id }}/logic/trace" class="tab {% if logic_tab == 'trace' %}active{% endif %}">Trace</a>
```

The file becomes:

```html
<nav class="subtabs">
  <a href="/controls/{{ control.id }}/logic/builder" class="tab {% if logic_tab == 'builder' %}active{% endif %}">Builder</a>
  <a href="/controls/{{ control.id }}/logic/ai" class="tab {% if logic_tab == 'ai' %}active{% endif %}">AI</a>
  <a href="/controls/{{ control.id }}/logic/flowchart" class="tab {% if logic_tab == 'flowchart' %}active{% endif %}">Flowchart</a>
  <a href="/controls/{{ control.id }}/logic/trace" class="tab {% if logic_tab == 'trace' %}active{% endif %}">Trace</a>
  <a href="/controls/{{ control.id }}/logic/python" class="tab {% if logic_tab == 'python' %}active{% endif %}">Python</a>
</nav>
```

- [ ] **Step 6: Create the template**

Create `uticen_lite/plane/templates/logic_trace.html`:

```html
{% extends "base.html" %}
{% block title %}{{ project.name }} — Trace: {{ control.title if control else control_id }}{% endblock %}
{% block head %}
<style>
  .trace-form { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0 4px; }
  .trace-form input[type=text] {
    font-size: 14px; padding: 6px 10px; min-width: 260px;
    background: var(--bg-input); color: var(--text-primary);
    border: 1px solid var(--border-default); border-radius: var(--radius-input);
  }
  .trace-examples { display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 12px; align-items: center; }
  .trace-examples .muted { font-size: 12px; }
  .trace-chip {
    font-family: var(--font-mono); font-size: 12px; padding: 2px 8px;
    border: 1px solid var(--border-default); border-radius: var(--radius-badge);
    background: var(--bg-surface-1); color: var(--text-secondary); text-decoration: none;
  }
  .trace-chip:hover { border-color: var(--accent-primary); color: var(--accent-primary); }
  .trace-steps { display: flex; flex-direction: column; gap: 0; margin: 12px 0; }
  .trace-step {
    display: flex; align-items: baseline; gap: 10px; padding: 8px 12px;
    border: 1px solid var(--border-default); border-bottom: none; background: var(--bg-surface-1);
  }
  .trace-step:last-child { border-bottom: 1px solid var(--border-default); }
  .trace-badge {
    font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .05em;
    padding: 2px 8px; border-radius: var(--radius-badge); white-space: nowrap;
  }
  .tb-present { background: var(--status-success-muted); color: var(--status-success); }
  .tb-dropped { background: var(--status-critical-muted); color: var(--status-critical); }
  .tb-absent  { background: var(--bg-surface-3); color: var(--text-tertiary); }
  .tb-indeterminate { background: var(--status-warning-muted); color: var(--status-warning); }
  .trace-step-label { font-weight: 600; }
  .trace-step-reason { color: var(--text-secondary); font-size: 13px; }
  .trace-verdict {
    margin: 16px 0 6px; padding: 12px 14px; border-radius: var(--radius-card);
    border: 1px solid var(--border-default); background: var(--bg-surface-1);
  }
  .trace-verdict.flagged { border-color: var(--status-critical); }
  .trace-verdict.passed  { border-color: var(--status-success); }
  .trace-cond-table { width: 100%; border-collapse: collapse; margin-top: 8px; }
  .trace-cond-table th, .trace-cond-table td {
    text-align: left; padding: 5px 8px; font-size: 13px;
    border-bottom: 1px solid var(--border-default);
  }
  .cond-pass { color: var(--status-success); font-weight: 600; }
  .cond-fail { color: var(--text-tertiary); }
  .cond-na   { color: var(--status-warning); }
</style>
{% endblock %}
{% block body %}
{% if control %}
{% set active = 'logic' %}{% include "partials/_control_header.html" %}
{% else %}
<a class="crumb" href="/controls/{{ control_id }}">← {{ control_id }}</a>
<div class="page-head"><h1>Logic</h1><p class="muted mono">{{ control_id }}</p></div>
{% endif %}
{% include "partials/_logic_tabs.html" %}

<p class="lead">Follow one record through the control's steps to see exactly where it
  is flagged — or why it passes. Type or paste a key value
  {% if trace and trace.key_column %}from the <strong>{{ trace.key_column }}</strong> column{% endif %}.</p>

<form class="trace-form" method="get" action="/controls/{{ control_id }}/logic/trace">
  <input type="text" name="key" value="{{ key }}" placeholder="Item key to trace"
         aria-label="Item key to trace" autofocus>
  <button class="btn btn-primary" type="submit">Trace</button>
</form>
{% if examples %}
<div class="trace-examples">
  <span class="muted">Try:</span>
  {% for ex in examples %}
  <a class="trace-chip" href="/controls/{{ control_id }}/logic/trace?key={{ ex | urlencode }}">{{ ex }}</a>
  {% endfor %}
</div>
{% endif %}

{% if message %}
<div class="callout callout-warn">{{ message }}</div>
{% endif %}
{% if trace and trace.message %}
<div class="callout callout-warn">{{ trace.message }}</div>
{% endif %}

{% if trace and not trace.message %}
  {% if trace.shared_count > 1 %}
  <div class="callout">{{ trace.shared_count }} records share this key; tracing the first.</div>
  {% endif %}

  <h2>Walk</h2>
  <div class="trace-steps">
    {% for s in trace.steps %}
    <div class="trace-step">
      <span class="trace-badge tb-{{ s.status }}">{{ s.status }}</span>
      <span class="trace-step-label">{{ s.label }}</span>
      {% if s.reason %}<span class="trace-step-reason">{{ s.reason }}</span>{% endif %}
    </div>
    {% endfor %}
  </div>

  {% for t in trace.tests %}
  {% if t.flagged is none %}{% set vclass = '' %}
  {% elif t.flagged %}{% set vclass = 'flagged' %}
  {% else %}{% set vclass = 'passed' %}{% endif %}
  <div class="trace-verdict {{ vclass }}">
    <strong>{{ t.label }}:</strong>
    {% if t.flagged is none %}
      <span class="muted">{{ t.note or "Verdict unavailable." }}</span>
    {% elif t.flagged %}
      Flagged as an exception.
    {% else %}
      Passed — not flagged.
    {% endif %}
    {% if t.note and t.flagged is not none %}
    <p class="muted" style="margin:6px 0 0;">{{ t.note }}</p>
    {% endif %}
    {% if t.conditions %}
    <table class="trace-cond-table">
      <thead><tr><th>Condition</th><th>Actual value</th><th>Result</th></tr></thead>
      <tbody>
        {% for c in t.conditions %}
        <tr>
          <td class="mono">{{ c.column }} {{ c.op }}{% if c.value is not none %} {{ c.value }}{% endif %}</td>
          <td class="mono">{{ c.actual }}</td>
          <td>
            {% if c.passed is none %}<span class="cond-na">— {{ c.note }}</span>
            {% elif c.passed %}<span class="cond-pass">✓ matched{% if c.note %} ({{ c.note }}){% endif %}</span>
            {% else %}<span class="cond-fail">✗ no match{% if c.note %} ({{ c.note }}){% endif %}</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% endif %}
  </div>
  {% endfor %}
{% endif %}
{% endblock %}
```

- [ ] **Step 7: Run the route tests**

Run: `python -m pytest tests/plane/test_logic_trace.py -q`
Expected: PASS (7 passed)

- [ ] **Step 8: Run the dev gates**

Run: `python -m ruff check . && python -m mypy uticen_lite && python -m pytest -q`
Expected: all green, no new warnings.

- [ ] **Step 9: Commit and push**

```bash
git add uticen_lite/plane/routes/pipeline.py \
        uticen_lite/plane/templates/logic_trace.html \
        uticen_lite/plane/templates/partials/_logic_tabs.html \
        tests/plane/test_logic_trace.py
git commit -m "feat(trace): Logic > Trace sub-tab route + template (#29)"
git push -u origin HEAD
```

---

## Task 3: Browser smoke (e2e)

**Files:**
- Create: `tests/e2e/test_record_trace_smoke.py`

**Interfaces:**
- Consumes: the `live_server` (str base URL) and `page` fixtures from `tests/e2e/conftest.py` / pytest-playwright; the `GET /controls/{id}/logic/trace` route from Task 2.

Per learning 0012 this trace adds a Logic surface, so it needs an e2e smoke. The `browser` marker is excluded from the fast lane (`--ignore=tests/e2e` in pyproject); CI runs it via `pytest tests/e2e -m browser` after `playwright install chromium`.

- [ ] **Step 1: Write the e2e smoke**

Create `tests/e2e/test_record_trace_smoke.py`:

```python
"""Browser smoke: type a key on Logic ▸ Trace → walk + verdict render (issue #29).

Run via: pytest tests/e2e -m browser   (after: playwright install chromium)
"""
from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser

_CSV = b"invoice_id,amount\nINV001,100\nINV005,500\n"


def _seed(page: Page, base: str) -> str:
    page.request.post(
        f"{base}/sources",
        multipart={
            "source_id": "inv_tr",
            "format": "csv",
            "file": {"name": "inv_tr.csv", "mimeType": "text/csv", "buffer": _CSV},
        },
    )
    # Mark invoice_id as the key column (uploads default to no key).
    page.request.post(
        f"{base}/sources/inv_tr",
        form={
            "key_columns": "invoice_id",
            "include__invoice_id": "on",
            "include__amount": "on",
        },
    )
    page.request.post(
        f"{base}/controls",
        form={
            "id": "tr_ctrl", "title": "Trace smoke", "objective": "o",
            "narrative": "n", "source_ids": "inv_tr", "failure_threshold_count": "0",
        },
    )
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "inv_tr"},
        {"id": "tst", "type": "test", "inputs": ["imp"], "config": {
            "logic": "all",
            "item_key_column": "invoice_id",
            "conditions": [{"column": "amount", "op": "gt", "value": 100}],
        }},
    ]}
    page.request.post(
        f"{base}/controls/tr_ctrl/logic/builder",
        form={"pipeline_json": json.dumps(graph)},
    )
    return "tr_ctrl"


@pytest.mark.browser
def test_trace_tab_flags_a_record(page: Page, live_server: str) -> None:
    base = live_server
    cid = _seed(page, base)

    page.goto(f"{base}/controls/{cid}/logic/trace")
    # The Trace tab is present and active.
    expect(page.get_by_role("link", name="Trace")).to_be_visible()

    # Type a flagged key and submit.
    page.get_by_label("Item key to trace").fill("INV005")
    page.get_by_role("button", name="Trace").click()

    # The verdict + a per-condition row render.
    expect(page.get_by_text("Flagged as an exception", exact=False)).to_be_visible()
    expect(page.get_by_text("matched", exact=False).first).to_be_visible()
```

- [ ] **Step 2: Run the e2e smoke**

Run: `python -m playwright install chromium >/dev/null 2>&1; python -m pytest tests/e2e/test_record_trace_smoke.py -m browser -q`
Expected: PASS (1 passed). If Playwright/browsers can't be installed in this environment, note it and move on (the route tests in Task 2 already cover the behavior).

- [ ] **Step 3: Run the fast lane to confirm no regression**

Run: `python -m ruff check . && python -m mypy uticen_lite && python -m pytest -q`
Expected: all green (the e2e lane is excluded from `pytest -q` by `--ignore=tests/e2e`).

- [ ] **Step 4: Commit and push**

```bash
git add tests/e2e/test_record_trace_smoke.py
git commit -m "test(trace): e2e browser smoke for Logic > Trace (#29)"
git push -u origin HEAD
```

---

## Self-Review (completed during planning)

**Spec coverage:**
- Dedicated `Logic ▸ Trace` sub-tab → Task 2 (route + nav + template).
- Type-in key + example chips → Task 2 template + route (`examples`).
- Full per-node walk (present/dropped/indeterminate) → Task 1 `trace_record` walk + Task 1 tests.
- Per-condition detail at each Test, reusing `_condition_mask` → Task 1 `_condition_rows`.
- `exists_in`/`not_exists_in` looked-up note → Task 1 `_xsrc_note` + test.
- Multiple Test terminals → Task 1 loops over `pipeline.terminals`.
- Non-unique key → Task 1 `shared_count` + template callout.
- Key not found / no key column → Task 1 messages + tests.
- Raw-Python degrade → Task 2 route + test.
- Unbound source degrade → Task 2 route ("Bind a data source").
- Never-500 → Task 2 route wrap + `test_bad_condition_column_never_500s`.
- Render-only (no bundle/store) → no schema files touched; trace is computed per request.
- e2e for a new Logic surface (0012) → Task 3.

**Placeholder scan:** none — every step carries full code/commands.

**Type consistency:** `trace_record(pipeline, frames, key, sources)` and the `TraceResult`/`NodeStep`/`TestStep`/`ConditionRow` field names used in Task 1 match the template fields read in Task 2 (`trace.key_column`, `trace.steps[].status/label/reason`, `trace.tests[].flagged/reached/note/conditions`, `condition.passed/actual/column/op/value/note`). `_load_source_populations` returns `dict[str, Population]` consumed by both the route and `trace_record`.
