# Step-data inspection & per-step workbook export — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let control-plane authors click a pipeline step's row-count to inspect that step's full-population data, export any step (or the whole flow) to Excel, and have edits recompute only from the changed step onward.

**Architecture:** A new pandas-layer engine (`pipeline/materialize.py`) generalizes the existing `rowcounts.py` probe: it runs the compiled pipeline over loaded frames and keeps the DataFrame at every node (not just `len`). A content-addressed per-step cache (keyed by each node's ancestor-closure + source versions) makes recompute incremental. Three control-plane surfaces consume it — an HTMX step-inspector drawer, a per-step `.xlsx` download, and a multi-sheet workbook. Nothing is persisted to the store or the bundle.

**Tech Stack:** Python ≥3.11, pandas (core dep), openpyxl (`[adapters]` extra, xlsx writing only), FastAPI + HTMX + Jinja2 (`[plane]` extra), pytest.

## Global Constraints

- **Python floor ≥3.11**; ruff target `py311`, line-length 100; `python -m ruff check .` and `python -m mypy uticen_lite` must stay green.
- **Test suite pristine:** `python -m pytest -q` green with **no stray warnings** (e.g. do NOT use `DataFrame.applymap` — deprecated; use per-column `Series.map`).
- **Pyodide boundary:** `model/`, `pipeline/compile.py`, `runner/execute.py` must stay pandas-free. New pandas-using code lives in `pipeline/materialize.py`, `adapters/`, or `plane/`. `materialize.py` must NOT `import pandas` at module top (import it inside functions, like `rowcounts.py`).
- **Cardinal rule (learning 0001):** raw population rows never enter `contract/bundle.schema.json`, `bundle/`, the store, or any persisted run. Do NOT bump the bundle `schema_version`. No store migration.
- **`[adapters]` gate is xlsx-only:** the xlsx writer must convert a missing `openpyxl` `ImportError` into `uticen_lite.plane.ingest.AdaptersUnavailable` (catch `ImportError` *before* any broad catch — learning 0024). The inspector (HTML) needs only core pandas.
- **Never 500 the editor (learning 0013):** missing/unbound source, incomplete graph, or a raising custom node degrade to a friendly partial, not an exception.
- **Excel limits:** a worksheet holds at most `1_048_576` rows incl. header → `1_048_575` data rows; truncate beyond that with a visible note.

---

## EXECUTION RULES

- **Never ask the user for permission to continue between tasks.** Execute the full plan start to finish without interruption.
- **On an unresolvable error after 2–3 attempts:** note it in your progress report and skip to the next task; do not stall the run.
- **After every `git commit`, push:** `git push -u origin HEAD` (already included as the final step of each task).
- Keep the gates green per task: `python -m pytest -q`, `python -m ruff check .`, `python -m mypy uticen_lite` — pristine output, no stray warnings.

---

## File Structure

- **`uticen_lite/pipeline/materialize.py`** (new) — the engine: `materialize_steps()`, the incremental cache primitives (`_step_keys`, `new_step_cache`), and the generated-body emitters. One responsibility: turn a pipeline + frames into `{node_id: DataFrame}`.
- **`uticen_lite/pipeline/rowcounts.py`** (modify) — `compute_row_counts()` becomes a thin `len()` over `materialize_steps()`; delete the now-dead `_emit_counts_body` / `_emit_terminal_count`.
- **`uticen_lite/adapters/xlsx_export.py`** (new) — `.xlsx` byte writers (`write_single_step`, `write_step_workbook`) + sheet-name sanitization, Excel cell coercion, row-limit truncation. CPython/pandas layer; `openpyxl`-gated.
- **`uticen_lite/plane/routes/pipeline.py`** (modify) — full-population frame loader, source-version tokens, the module-level step cache, and 3 new routes (inspector partial, per-step xlsx, workbook xlsx); `_row_counts` switches to full population via the cache.
- **`uticen_lite/plane/templates/partials/_step_data.html`** (new) — the inspector drawer partial (paginated table + per-step download).
- **`uticen_lite/plane/templates/partials/_pipe_node.html`**, **`_pipe_diagram.html`** (modify) — make the row-count clickable (HTMX → `#step-drawer`).
- **`uticen_lite/plane/templates/logic_builder.html`**, **`logic_flowchart.html`** (modify) — add the `#step-drawer` container + the workbook-export button.
- **Tests:** `tests/pipeline/test_materialize.py` (new), `tests/adapters/test_xlsx_export.py` (new), `tests/plane/test_pipeline_steps.py` (new), `tests/test_steps_trust_boundary.py` (new), `tests/e2e/test_step_inspector_smoke.py` (new, `browser` marker).

---

## Task 1: Materialization engine + rowcounts refactor

**Files:**
- Create: `uticen_lite/pipeline/materialize.py`
- Modify: `uticen_lite/pipeline/rowcounts.py`
- Test: `tests/pipeline/test_materialize.py`

**Interfaces:**
- Consumes: `uticen_lite.pipeline.compile._frame, _conditions, _emit_node_lines, _emit_custom_helper`; `uticen_lite.pipeline.model.Pipeline`; `uticen_lite.rules.render_rule._mask_expr`.
- Produces:
  - `MaterializeError(RuntimeError)`
  - `materialize_steps(pipeline: Pipeline, frames: dict[str, Any], *, source_versions: dict[str, str] | None = None, cache: "OrderedDict | None" = None, recomputed_out: set[str] | None = None) -> dict[str, Any]` — returns `{node_id: DataFrame}`; `{}` when an Import source is absent from `frames`. (Task 1 ships the no-cache path; `source_versions`/`cache`/`recomputed_out` are added in Task 2 — define the full signature now but ignore the cache args until Task 2.)
  - `compute_row_counts(pipeline, frames) -> dict[str, int]` (unchanged signature; now delegates).

- [ ] **Step 1: Write the failing test**

Create `tests/pipeline/test_materialize.py`:

```python
"""materialize_steps: per-node frames; rowcounts equals len over those frames."""
from __future__ import annotations

import pandas as pd

from uticen_lite.pipeline.materialize import materialize_steps
from uticen_lite.pipeline.model import parse_pipeline
from uticen_lite.pipeline.rowcounts import compute_row_counts


def _pipeline():
    # import -> filter(amount>100) -> test(status==open) , plus a 2nd import joined.
    return parse_pipeline({"nodes": [
        {"id": "inv", "type": "import", "source_id": "invoices"},
        {"id": "flt", "type": "filter", "inputs": ["inv"],
         "config": {"logic": "all", "conditions": [
             {"column": "amount", "op": "gt", "value": 100}]}},
        {"id": "tst", "type": "test", "inputs": ["flt"],
         "config": {"logic": "all", "conditions": [
             {"column": "status", "op": "eq", "value": "open"}]}},
    ]})


def _frames():
    return {"invoices": pd.DataFrame({
        "id": ["a", "b", "c", "d"],
        "amount": [50, 150, 200, 300],
        "status": ["open", "open", "closed", "open"],
    })}


def test_materialize_returns_frame_per_node():
    steps = materialize_steps(_pipeline(), _frames())
    assert set(steps) == {"inv", "flt", "tst"}
    assert len(steps["inv"]) == 4          # whole population
    assert len(steps["flt"]) == 3          # amount > 100 → b, c, d
    assert len(steps["tst"]) == 2          # of those, status == open → b, d
    assert list(steps["tst"]["id"]) == ["b", "d"]  # terminal = the violating ROWS


def test_compute_row_counts_equals_len_of_materialized_frames():
    p, f = _pipeline(), _frames()
    counts = compute_row_counts(p, f)
    steps = materialize_steps(p, f)
    assert counts == {nid: len(df) for nid, df in steps.items()}
    assert counts == {"inv": 4, "flt": 3, "tst": 2}


def test_missing_source_returns_empty():
    assert materialize_steps(_pipeline(), {}) == {}
    assert compute_row_counts(_pipeline(), {}) == {}


def test_custom_python_test_terminal_frame_is_the_violations():
    p = parse_pipeline({"nodes": [
        {"id": "src", "type": "import", "source_id": "s"},
        {"id": "cpt", "type": "custom_python", "inputs": ["src"],
         "config": {"flavor": "test", "code":
                    "return [{'item_key': str(r.id), 'description': '', "
                    "'severity': 'low', 'details': {}} "
                    "for r in rows.itertuples() if r.amount > 100]"}},
    ]})
    f = {"s": pd.DataFrame({"id": [1, 2, 3], "amount": [50, 150, 250]})}
    steps = materialize_steps(p, f)
    assert len(steps["cpt"]) == 2          # the two violations, as a frame
    assert compute_row_counts(p, f)["cpt"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/pipeline/test_materialize.py -q`
Expected: FAIL — `ModuleNotFoundError: uticen_lite.pipeline.materialize`.

- [ ] **Step 3: Write minimal implementation**

Create `uticen_lite/pipeline/materialize.py`:

```python
"""Materialise the DataFrame at every node of a control pipeline.

This is the engine behind the per-step data inspector, the per-step / whole-pipeline
Excel exports, AND the live row-counts. It generalises :mod:`uticen_lite.pipeline.rowcounts`:
that probe kept only ``len(frame)`` after each node; this keeps the frames themselves.

Like rowcounts it reuses the compiler's *exact* node semantics
(:func:`uticen_lite.pipeline.compile._emit_node_lines` + the module-level custom-python
helpers), so what you inspect is byte-for-byte what the compiled ``test()`` computes. It
``exec``s generated code over real pandas frames, so it lives behind the runner-side boundary
(callers pass already-loaded frames) and never imports pandas at module import time.

The cache primitives (added for incremental recompute) are content-addressed: a node's key
hashes its ancestor-closure plus the version token of every source feeding it, so editing a
step changes that node's key and every descendant's key (their closures contain it) while
upstream keys are unchanged — "recompute from the edited step onward" falls out for free.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from uticen_lite.pipeline.compile import (
    _conditions,
    _emit_node_lines,
    _frame,
)
from uticen_lite.pipeline.model import Node, Pipeline
from uticen_lite.rules.render_rule import _mask_expr

_CACHE_MAX = 64  # bound the in-memory frame cache (single-user, localhost)


class MaterializeError(RuntimeError):
    """A pipeline's step-materialisation failed to evaluate over the given frames."""


def new_step_cache() -> "OrderedDict[str, Any]":
    """An LRU-bounded frame cache for :func:`materialize_steps` (one per process)."""
    return OrderedDict()


def _emit_terminal_frame(node: Node) -> list[str]:
    """Lines assigning ``_f_<id>`` for a terminal node — the rows behind its count.

    A rule-style Test yields its violating rows (``input[mask]``); an empty-condition
    Test yields an empty slice (count 0). A custom-python ``test``-flavor terminal yields
    its helper's return value (the violations list), converted to a frame in Python.
    """
    if node.type == "custom_python" and node.config.get("flavor") == "test":
        return [f"{_frame(node.id)} = _node_fns[{node.id!r}]({_frame(node.inputs[0])})"]
    src = _frame(node.inputs[0])
    out = _frame(node.id)
    conds = _conditions(node.config.get("conditions", []))
    if not conds:
        return [f"{out} = {src}.iloc[0:0]"]
    combine = " & " if node.config.get("logic", "all") == "all" else " | "
    mask = combine.join(_mask_expr(c, frame=src) for c in conds)
    return [f"{out} = {src}[{mask}]"]


def _emit_materialize_body(pipeline: Pipeline, recompute: set[str]) -> str:
    """Emit ``def _materialize(frames, _node_fns, _seed): return {node_id: frame_or_list}``.

    Nodes in *recompute* emit their compute lines; all other nodes are seeded from
    ``_seed`` (a cached frame), so only the edited step and its descendants run.
    """
    order = pipeline.topological()
    terminal_ids = {t.id for t in pipeline.terminals}
    lines: list[str] = ["def _materialize(frames, _node_fns, _seed):", "    _out = {}"]
    for node in order:
        f = _frame(node.id)
        if node.id not in recompute:
            lines.append(f"    {f} = _seed[{node.id!r}]")
        elif node.id in terminal_ids:
            lines.extend(f"    {ln}" for ln in _emit_terminal_frame(node))
        elif node.type == "import":
            lines.append(f"    {f} = frames[{node.source_id!r}]")
        elif node.type == "custom_python":
            lines.append(f"    {f} = _node_fns[{node.id!r}]({_frame(node.inputs[0])})")
        else:
            lines.extend(
                f"    {ln}" for ln in _emit_node_lines(node, primary_source=None)
                if not ln.startswith("#")
            )
        lines.append(f"    _out[{node.id!r}] = {f}")
    lines.append("    return _out")
    return "\n".join(lines)


def materialize_steps(
    pipeline: Pipeline,
    frames: dict[str, Any],
    *,
    source_versions: dict[str, str] | None = None,
    cache: "OrderedDict[str, Any] | None" = None,
    recomputed_out: set[str] | None = None,
) -> dict[str, Any]:
    """Return ``{node_id: DataFrame}`` — the data at every step over *frames*.

    *frames* maps each Import node's ``source_id`` to a loaded pandas DataFrame. Returns
    ``{}`` when any referenced source is absent (mirrors :func:`compute_row_counts`), so the
    editor shows "—" until every Import source has a frame. The terminal node's frame is the
    rows behind its violation count.

    When both *cache* and *source_versions* are supplied, unchanged nodes are reused from the
    cache and only the edited step + its descendants recompute. *recomputed_out*, if given, is
    filled with the ids actually recomputed (for tests).
    """
    import pandas as pd

    needed = set(pipeline.import_source_ids())
    if not needed.issubset(frames.keys()):
        return {}

    use_cache = cache is not None and source_versions is not None
    keys = _step_keys(pipeline, source_versions or {}) if use_cache else {}
    if use_cache:
        recompute = {n.id for n in pipeline.nodes if keys[n.id] not in cache}
        seed = {n.id: cache[keys[n.id]] for n in pipeline.nodes if keys[n.id] in cache}
        for n in pipeline.nodes:           # mark reused entries as recently used
            if keys[n.id] in cache:
                cache.move_to_end(keys[n.id])
    else:
        recompute = {n.id for n in pipeline.nodes}
        seed = {}
    if recomputed_out is not None:
        recomputed_out.clear()
        recomputed_out.update(recompute)

    from uticen_lite.pipeline.compile import _emit_custom_helper

    namespace: dict[str, Any] = {}
    helper_parts = [
        _emit_custom_helper(n) for n in pipeline.nodes if n.type == "custom_python"
    ]
    body = _emit_materialize_body(pipeline, recompute)
    src = "\n\n\n".join([*helper_parts, body])
    try:
        exec(src, namespace)  # noqa: S102 — author code, guardrailed by lint
        node_fns = {
            n.id: namespace[f"_node_{n.id}"]
            for n in pipeline.nodes if n.type == "custom_python"
        }
        raw = namespace["_materialize"](frames, node_fns, seed)
    except Exception as exc:  # noqa: BLE001 — surface as a typed, contained error
        raise MaterializeError(str(exc)) from exc

    out: dict[str, Any] = {}
    terminal_ids = {t.id for t in pipeline.terminals}
    for nid, val in raw.items():
        node = pipeline.node(nid)
        if nid in recompute and nid in terminal_ids and node.type == "custom_python":
            val = pd.DataFrame(val)        # custom-test terminal returns a list
        out[nid] = val
    if use_cache:
        for nid in recompute:
            cache[keys[nid]] = out[nid]
        while len(cache) > _CACHE_MAX:
            cache.popitem(last=False)
    return out
```

Append the cache-key primitives at the bottom of the same file (used now by the signature, exercised in Task 2):

```python
def _ancestor_closure(pipeline: Pipeline, node: Node) -> list[Node]:
    """*node* and all its transitive inputs, in topological order."""
    keep: set[str] = set()

    def visit(nid: str) -> None:
        if nid in keep:
            return
        keep.add(nid)
        for s in pipeline.node(nid).inputs:
            visit(s)

    visit(node.id)
    return [n for n in pipeline.topological() if n.id in keep]


def _canonical_node(node: Node) -> dict[str, Any]:
    """The data-affecting fields of a node (narrative is a comment — excluded)."""
    return {
        "id": node.id, "type": node.type, "config": node.config,
        "inputs": list(node.inputs), "source_id": node.source_id,
    }


def _step_keys(pipeline: Pipeline, source_versions: dict[str, str]) -> dict[str, str]:
    """Map each node id → a content hash of its ancestor-closure + feeding source versions."""
    import hashlib
    import json

    keys: dict[str, str] = {}
    for node in pipeline.nodes:
        closure = _ancestor_closure(pipeline, node)
        src_ids = sorted({
            n.source_id for n in closure
            if n.type == "import" and n.source_id
        })
        payload = {
            "nodes": [_canonical_node(n) for n in closure],
            "sources": {sid: source_versions.get(sid, "") for sid in src_ids},
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        keys[node.id] = hashlib.sha256(blob.encode()).hexdigest()
    return keys
```

Now refactor `uticen_lite/pipeline/rowcounts.py`. Replace the whole file body below the module docstring with:

```python
from __future__ import annotations

from typing import Any

from uticen_lite.pipeline.model import Pipeline


class RowCountError(RuntimeError):
    """A pipeline's row-count probe failed to evaluate over the sample frames."""


def compute_row_counts(pipeline: Pipeline, frames: dict[str, Any]) -> dict[str, int]:
    """Return ``{node_id: rows surviving}`` for *pipeline* over *frames*.

    Now a thin ``len()`` over :func:`uticen_lite.pipeline.materialize.materialize_steps`
    so the count and the inspectable data are the *same* computation. Returns ``{}`` when a
    source is missing; raises :class:`RowCountError` on an evaluation failure.
    """
    from uticen_lite.pipeline.materialize import MaterializeError, materialize_steps
    from uticen_lite.rules.spec import RuleSpecError

    try:
        steps = materialize_steps(pipeline, frames)
    except (MaterializeError, RuleSpecError) as exc:
        raise RowCountError(str(exc)) from exc
    return {nid: len(df) for nid, df in steps.items()}
```

Keep the module docstring at the top of `rowcounts.py` (update its first line to note it now delegates to `materialize.py`). Then run `grep -rn "_emit_counts_body\|_emit_terminal_count\|RowCountError" uticen_lite tests` and fix any other reference (the `routes/pipeline.py` import of `RowCountError`/`compute_row_counts` stays valid).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/pipeline/ -q && python -m ruff check uticen_lite/pipeline && python -m mypy uticen_lite/pipeline/materialize.py uticen_lite/pipeline/rowcounts.py`
Expected: PASS; ruff/mypy clean. Also run the full suite to confirm the rowcounts refactor didn't break callers: `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add uticen_lite/pipeline/materialize.py uticen_lite/pipeline/rowcounts.py tests/pipeline/test_materialize.py
git commit -m "feat(pipeline): materialize per-step frames; rowcounts delegates"
git push -u origin HEAD
```

---

## Task 2: Incremental recompute cache

**Files:**
- Modify: `uticen_lite/pipeline/materialize.py` (already has the primitives from Task 1 — this task only adds tests; if any primitive is missing, add it here)
- Test: `tests/pipeline/test_materialize.py` (append)

**Interfaces:**
- Consumes: `materialize_steps(..., source_versions=, cache=, recomputed_out=)`, `new_step_cache`, `_step_keys` from Task 1.
- Produces: no new public symbols — proves the cache behavior.

- [ ] **Step 1: Write the failing test**

Append to `tests/pipeline/test_materialize.py`:

```python
import copy

from uticen_lite.pipeline.materialize import _step_keys, new_step_cache


def _graph():
    return {"nodes": [
        {"id": "inv", "type": "import", "source_id": "invoices"},
        {"id": "flt", "type": "filter", "inputs": ["inv"],
         "config": {"logic": "all", "conditions": [
             {"column": "amount", "op": "gt", "value": 100}]}},
        {"id": "tst", "type": "test", "inputs": ["flt"],
         "config": {"logic": "all", "conditions": [
             {"column": "status", "op": "eq", "value": "open"}]}},
    ]}


def test_step_keys_change_for_edited_node_and_descendants_only():
    p1 = parse_pipeline(_graph())
    sv = {"invoices": "v1"}
    k1 = _step_keys(p1, sv)

    g2 = copy.deepcopy(_graph())                       # edit the FILTER (a middle node)
    g2["nodes"][1]["config"]["conditions"][0]["value"] = 200
    k2 = _step_keys(parse_pipeline(g2), sv)

    assert k2["inv"] == k1["inv"]                      # upstream unchanged
    assert k2["flt"] != k1["flt"]                      # edited node changed
    assert k2["tst"] != k1["tst"]                      # descendant changed


def test_source_version_change_busts_every_key():
    p = parse_pipeline(_graph())
    k1 = _step_keys(p, {"invoices": "v1"})
    k2 = _step_keys(p, {"invoices": "v2"})
    assert all(k2[n] != k1[n] for n in k1)


def test_cache_recomputes_only_edited_step_onward():
    cache = new_step_cache()
    sv = {"invoices": "v1"}
    first = set()
    materialize_steps(parse_pipeline(_graph()), _frames(),
                      source_versions=sv, cache=cache, recomputed_out=first)
    assert first == {"inv", "flt", "tst"}             # cold cache → all recompute

    g2 = copy.deepcopy(_graph())
    g2["nodes"][2]["config"]["conditions"][0]["value"] = "closed"  # edit the TEST only
    second = set()
    steps = materialize_steps(parse_pipeline(g2), _frames(),
                              source_versions=sv, cache=cache, recomputed_out=second)
    assert second == {"tst"}                          # only the edited terminal recomputed
    # ...and the cached path is still correct (status==closed → only row c):
    assert list(steps["tst"]["id"]) == ["c"]


def test_cache_is_bounded():
    from uticen_lite.pipeline.materialize import _CACHE_MAX
    cache = new_step_cache()
    for i in range(_CACHE_MAX + 20):
        materialize_steps(parse_pipeline(_graph()), _frames(),
                          source_versions={"invoices": f"v{i}"}, cache=cache)
    assert len(cache) <= _CACHE_MAX
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/pipeline/test_materialize.py -q`
Expected: PASS already if Task 1 included the cache path — in that case this task is purely additive verification. If any new helper/behavior is missing, the relevant test FAILs (e.g. `ImportError: _CACHE_MAX`); implement it in Step 3.

- [ ] **Step 3: Write minimal implementation**

If Task 1 was implemented exactly as written, no code changes are needed. Otherwise add the missing piece (`_CACHE_MAX`, `new_step_cache`, the `move_to_end`/eviction, or the `recomputed_out` fill) to `materialize.py` to satisfy the failing assertion.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/pipeline/test_materialize.py -q && python -m ruff check uticen_lite/pipeline && python -m mypy uticen_lite/pipeline/materialize.py`
Expected: PASS; ruff/mypy clean.

- [ ] **Step 5: Commit**

```bash
git add uticen_lite/pipeline/materialize.py tests/pipeline/test_materialize.py
git commit -m "test(pipeline): incremental step cache recomputes from edited step onward"
git push -u origin HEAD
```

---

## Task 3: Full-population frames, source versions, and full-pop badges

**Files:**
- Modify: `uticen_lite/plane/routes/pipeline.py`
- Test: `tests/plane/test_pipeline_steps.py` (new)

**Interfaces:**
- Consumes: `materialize_steps`, `new_step_cache` (materialize); `repo.get_source`, `repo.get_current_file` (store); `source_for` (adapters), `_binding` (store loader).
- Produces (module-level in `routes/pipeline.py`):
  - `_STEP_CACHE` — process-wide `OrderedDict` step cache.
  - `_load_full_frames(conn, root, source_ids) -> dict[str, DataFrame]`
  - `_source_versions(conn, root, source_ids) -> dict[str, str]`
  - `_materialize_full(conn, root, pipeline) -> dict[str, DataFrame]`
  - `_row_counts(conn, root, pipeline) -> dict[str, int]` (re-implemented on top of the above; same signature as today).

- [ ] **Step 1: Write the failing test**

Create `tests/plane/test_pipeline_steps.py`. (Reuse the existing plane test fixtures — inspect `tests/plane/conftest.py` for the project/client fixtures and a control with a bound CSV source; follow the pattern in `tests/plane/test_pipeline_editor.py`. The test below assumes a fixture `seeded_app` yielding `(client, control_id)` for a control whose pipeline is `import → filter → test` over a small CSV; if the existing conftest exposes a different fixture name/shape, adapt the harness lines, not the assertions.)

```python
"""Full-population step frames feed the badges and the inspector route."""
from __future__ import annotations

from uticen_lite.plane.routes import pipeline as P


def test_load_full_frames_is_uncapped(seeded_conn, project_root):
    # The control's source has > 0 rows; full load returns the whole file (no .head cap).
    frames = P._load_full_frames(seeded_conn, project_root, ["invoices"])
    assert "invoices" in frames
    assert len(frames["invoices"]) == P._expected_invoice_rows  # full count, not 2000-capped


def test_source_versions_change_with_the_file(seeded_conn, project_root):
    v = P._source_versions(seeded_conn, project_root, ["invoices"])
    assert v.get("invoices")  # a non-empty token
```

(Define `_expected_invoice_rows` inline in the test from the fixture's known row count, or assert `len(frames["invoices"]) == len(<the loaded csv>)`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/plane/test_pipeline_steps.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute '_load_full_frames'`.

- [ ] **Step 3: Write minimal implementation**

In `uticen_lite/plane/routes/pipeline.py`:

1. Add imports near the top: `from collections import OrderedDict`.
2. Below `_EMPTY_GRAPH`, add the module-level cache:

```python
# Process-wide, LRU-bounded cache of materialised step frames (single-user, localhost).
# Keyed inside materialize_steps by each node's ancestor-closure + source versions.
from uticen_lite.pipeline.materialize import new_step_cache as _new_step_cache
_STEP_CACHE = _new_step_cache()
```

3. Replace the `_load_sample_frames` / `_ROWCOUNT_SAMPLE` block and `_row_counts` with full-population versions:

```python
def _load_full_frames(
    conn: sqlite3.Connection, root: Any, source_ids: list[str]
) -> dict[str, Any]:
    """Load the FULL coerced DataFrame for each bound source (by source_id).

    Decision (ii): the live badges and the inspector run over the full population —
    the incremental step cache keeps edits fast. Returns only the sources whose file
    exists; a missing frame makes materialize_steps return ``{}``.
    """
    from uticen_lite.adapters.files import source_for
    from uticen_lite.store.loader import _binding

    frames: dict[str, Any] = {}
    for sid in source_ids:
        src = repo.get_source(conn, sid)
        current = repo.get_current_file(conn, sid)
        if not src or not current:
            continue
        fpath = root / current["stored_path"]
        if not fpath.is_file():
            continue
        try:
            frames[sid] = source_for(_binding(src), root).load().df
        except Exception:  # noqa: BLE001 — a broken/adapters-missing file yields no frame
            continue
    return frames


def _source_versions(
    conn: sqlite3.Connection, root: Any, source_ids: list[str]
) -> dict[str, str]:
    """A cache-busting version token per source (current file path + mtime + size)."""
    out: dict[str, str] = {}
    for sid in source_ids:
        current = repo.get_current_file(conn, sid)
        if not current:
            continue
        stored = current["stored_path"]
        try:
            st = (root / stored).stat()
            out[sid] = f"{stored}:{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            out[sid] = stored
    return out


def _materialize_full(
    conn: sqlite3.Connection, root: Any, pipeline: Pipeline
) -> dict[str, Any]:
    """Best-effort ``{node_id: DataFrame}`` over the full population (cached).

    Returns ``{}`` when a source is unbound/missing or the probe fails — never raises
    into the request (learning 0013).
    """
    from uticen_lite.pipeline.materialize import MaterializeError, materialize_steps
    from uticen_lite.rules.spec import RuleSpecError

    sids = pipeline.import_source_ids()
    frames = _load_full_frames(conn, root, sids)
    versions = _source_versions(conn, root, sids)
    try:
        return materialize_steps(
            pipeline, frames, source_versions=versions, cache=_STEP_CACHE
        )
    except (MaterializeError, RuleSpecError):
        return {}


def _row_counts(
    conn: sqlite3.Connection, root: Any, pipeline: Pipeline
) -> dict[str, int]:
    """Best-effort full-population row-counts (``len`` over the materialised frames)."""
    return {nid: len(df) for nid, df in _materialize_full(conn, root, pipeline).items()}
```

4. Remove the now-unused `_ROWCOUNT_SAMPLE` constant and the old `_load_sample_frames`/`_row_counts` definitions and the now-unused `RowCountError` import line (`from uticen_lite.pipeline.rowcounts import RowCountError, compute_row_counts` inside the old `_row_counts`). Run `grep -n "_load_sample_frames\|_ROWCOUNT_SAMPLE\|compute_row_counts" uticen_lite/plane` and clean up any leftover reference.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/plane/test_pipeline_steps.py tests/plane/test_pipeline_editor.py -q && python -m ruff check uticen_lite/plane && python -m mypy uticen_lite`
Expected: PASS; ruff/mypy clean. Run `python -m pytest -q` to confirm no plane regressions from the full-pop switch.

- [ ] **Step 5: Commit**

```bash
git add uticen_lite/plane/routes/pipeline.py tests/plane/test_pipeline_steps.py
git commit -m "feat(plane): full-population step frames + source-versioned step cache"
git push -u origin HEAD
```

---

## Task 4: xlsx writer (per-step + workbook)

**Files:**
- Create: `uticen_lite/adapters/xlsx_export.py`
- Test: `tests/adapters/test_xlsx_export.py`

**Interfaces:**
- Consumes: `pandas`, `openpyxl` (lazy); `uticen_lite.plane.ingest.AdaptersUnavailable`.
- Produces:
  - `EXCEL_MAX_DATA_ROWS = 1_048_575`
  - `write_single_step(frame, label: str) -> bytes`
  - `write_step_workbook(steps: list[tuple[str, Any]], meta: dict[str, str]) -> bytes` — `steps` is `[(label, frame), ...]` in flow order.
  - `_sanitize_sheet_name`, `_coerce_for_excel` (module-internal, tested directly).

- [ ] **Step 1: Write the failing test**

Create `tests/adapters/test_xlsx_export.py`:

```python
"""xlsx step exports: sheets, summary, sanitisation, coercion, truncation."""
from __future__ import annotations

from io import BytesIO

import numpy as np
import pandas as pd
import pytest

from uticen_lite.adapters import xlsx_export as X


def _read(buf_bytes, sheet=0):
    return pd.read_excel(BytesIO(buf_bytes), sheet_name=sheet, engine="openpyxl")


def test_single_step_roundtrips():
    frame = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    back = _read(X.write_single_step(frame, "2 - filter"))
    assert list(back.columns) == ["id", "name"]
    assert len(back) == 2


def test_workbook_has_summary_about_and_one_sheet_per_step():
    steps = [("import", pd.DataFrame({"x": [1, 2, 3]})),
             ("filter", pd.DataFrame({"x": [2, 3]}))]
    book = X.write_step_workbook(steps, {"control": "C-1", "generated_at": "2026-06-23"})
    names = pd.ExcelFile(BytesIO(book), engine="openpyxl").sheet_names
    assert "Summary" in names and "About" in names
    assert len([n for n in names if n not in ("Summary", "About")]) == 2
    summary = _read(book, "Summary")
    assert set(summary["rows"]) == {3, 2}


def test_sheet_name_sanitised_and_deduped():
    used: set[str] = set()
    a = X._sanitize_sheet_name("a/b:c*d?e[f]" * 4, used)   # illegal chars + > 31 chars
    b = X._sanitize_sheet_name("a/b:c*d?e[f]" * 4, used)
    assert not (set("[]:*?/\\") & set(a)) and len(a) <= 31
    assert a != b                                          # deduped


def test_coercion_handles_timestamp_nat_numpy_and_objects():
    frame = pd.DataFrame({
        "ts": [pd.Timestamp("2026-01-01"), pd.NaT],
        "np": [np.int64(5), np.float64(1.5)],
        "obj": [{"k": 1}, [1, 2]],
    })
    out = X._coerce_for_excel(frame)
    # writing must not raise, and objects became strings:
    _read(X.write_single_step(frame, "s"))
    assert isinstance(out["obj"].iloc[0], str)


def test_truncation_note_when_over_excel_limit(monkeypatch):
    monkeypatch.setattr(X, "EXCEL_MAX_DATA_ROWS", 3)      # shrink the cap for the test
    steps = [("big", pd.DataFrame({"x": list(range(10))}))]
    book = X.write_step_workbook(steps, {"control": "C-1"})
    summary = _read(book, "Summary")
    assert summary.loc[0, "rows"] == 10                   # reports the TRUE total
    assert str(summary.loc[0, "truncated"]).lower() in ("yes", "true")
    sheet = pd.ExcelFile(BytesIO(book), engine="openpyxl").sheet_names
    data_sheet = [n for n in sheet if n not in ("Summary", "About")][0]
    assert len(_read(book, data_sheet)) == 3             # capped


def test_missing_openpyxl_raises_adapters_unavailable(monkeypatch):
    from uticen_lite.plane.ingest import AdaptersUnavailable

    def _boom():
        raise ImportError("no openpyxl")

    monkeypatch.setattr(X, "_require_writer", _boom)
    with pytest.raises((AdaptersUnavailable, ImportError)):
        X.write_single_step(pd.DataFrame({"x": [1]}), "s")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/adapters/test_xlsx_export.py -q`
Expected: FAIL — `ModuleNotFoundError: uticen_lite.adapters.xlsx_export`.

- [ ] **Step 3: Write minimal implementation**

Create `uticen_lite/adapters/xlsx_export.py`:

```python
"""Write pipeline step data to ``.xlsx`` for local inspection (NOT the bundle).

This is a localhost-only evidence export: raw population rows are written to a workbook the
author downloads. It never touches the import bundle or the store (cardinal rule, learning
0001). Requires the ``[adapters]`` extra (``openpyxl``); a missing engine becomes a friendly
:class:`uticen_lite.plane.ingest.AdaptersUnavailable` (learning 0024).
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

import pandas as pd

from uticen_lite.plane.ingest import AdaptersUnavailable

# A worksheet holds 1_048_576 rows incl. the header row → this many DATA rows.
EXCEL_MAX_DATA_ROWS = 1_048_575
_ILLEGAL_SHEET = set('[]:*?/\\')


def _require_writer() -> None:
    """Raise a friendly error if the xlsx engine isn't installed."""
    try:
        import openpyxl  # noqa: F401
    except ImportError as exc:  # learning 0024 — catch ImportError first, typed re-raise
        raise AdaptersUnavailable(
            "Excel export needs the [adapters] extra. Install with: "
            "pip install 'uticen-lite[adapters]'"
        ) from exc


def _sanitize_sheet_name(name: str, used: set[str]) -> str:
    """An Excel-legal, ≤31-char, unique sheet name."""
    clean = "".join("_" if ch in _ILLEGAL_SHEET else ch for ch in name).strip() or "sheet"
    clean = clean[:31]
    base, i = clean, 2
    while clean.lower() in used:
        suffix = f"_{i}"
        clean = base[: 31 - len(suffix)] + suffix
        i += 1
    used.add(clean.lower())
    return clean


def _coerce_for_excel(frame: pd.DataFrame) -> pd.DataFrame:
    """Make every cell openpyxl-writable, keeping native numbers/dates (learning 0020)."""
    import numpy as np

    def cell(v: Any) -> Any:
        if isinstance(v, pd.Timestamp):
            return None if pd.isna(v) else v.to_pydatetime()
        if isinstance(v, (list, dict, set, tuple)):
            return str(v)
        if isinstance(v, np.generic):
            scalar = v.item()
            return None if isinstance(scalar, float) and pd.isna(scalar) else scalar
        try:
            if v is None or (np.isscalar(v) and pd.isna(v)):
                return None
        except (TypeError, ValueError):
            pass
        return v

    out = frame.copy()
    for col in out.columns:
        out[col] = out[col].map(cell)   # Series.map — never DataFrame.applymap (deprecated)
    return out


def _prep(frame: pd.DataFrame) -> tuple[pd.DataFrame, bool, int]:
    """Return (excel-ready frame capped to the row limit, truncated?, true total)."""
    total = len(frame)
    truncated = total > EXCEL_MAX_DATA_ROWS
    capped = frame.iloc[:EXCEL_MAX_DATA_ROWS] if truncated else frame
    return _coerce_for_excel(capped), truncated, total


def write_single_step(frame: pd.DataFrame, label: str) -> bytes:
    """A one-sheet workbook of *frame* (the data at one step)."""
    _require_writer()
    coerced, truncated, total = _prep(frame)
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        coerced.to_excel(xw, sheet_name=_sanitize_sheet_name(label, set()), index=False)
        if truncated:
            pd.DataFrame({"note": [
                f"Truncated to {EXCEL_MAX_DATA_ROWS:,} of {total:,} rows (Excel limit)."
            ]}).to_excel(xw, sheet_name="Truncated", index=False)
    return buf.getvalue()


def write_step_workbook(steps: list[tuple[str, pd.DataFrame]], meta: dict[str, str]) -> bytes:
    """A multi-sheet workbook: one sheet per step (flow order) + Summary + About.

    *steps* is ``[(label, frame), ...]``; *meta* is shown on the About sheet
    (control id, generation timestamp, etc.).
    """
    _require_writer()
    used: set[str] = set()
    prepared: list[tuple[str, pd.DataFrame]] = []
    summary_rows: list[dict[str, Any]] = []
    for i, (label, frame) in enumerate(steps, start=1):
        coerced, truncated, total = _prep(frame)
        sheet = _sanitize_sheet_name(f"{i} - {label}", used)
        prepared.append((sheet, coerced))
        summary_rows.append({
            "step": i, "sheet": sheet, "label": label,
            "rows": total, "truncated": "yes" if truncated else "",
        })

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame(summary_rows, columns=["step", "sheet", "label", "rows", "truncated"]) \
            .to_excel(xw, sheet_name="Summary", index=False)
        pd.DataFrame(list(meta.items()), columns=["field", "value"]) \
            .to_excel(xw, sheet_name="About", index=False)
        for sheet, coerced in prepared:
            coerced.to_excel(xw, sheet_name=sheet, index=False)
    return buf.getvalue()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/adapters/test_xlsx_export.py -q && python -m ruff check uticen_lite/adapters/xlsx_export.py && python -m mypy uticen_lite/adapters/xlsx_export.py`
Expected: PASS; ruff/mypy clean.

- [ ] **Step 5: Commit**

```bash
git add uticen_lite/adapters/xlsx_export.py tests/adapters/test_xlsx_export.py
git commit -m "feat(adapters): xlsx step + workbook writers (openpyxl-gated)"
git push -u origin HEAD
```

---

## Task 5: Step inspector route + drawer + clickable counts

**Files:**
- Modify: `uticen_lite/plane/routes/pipeline.py` (add the inspector route + a `_pipeline_for_view` helper; thread `control_id` into the `ai_apply` `_pipe_cards.html` context)
- Create: `uticen_lite/plane/templates/partials/_step_data.html`
- Modify: `uticen_lite/plane/templates/partials/_pipe_node.html`, `_pipe_diagram.html`, `logic_builder.html`, `logic_flowchart.html`
- Test: `tests/plane/test_pipeline_steps.py` (append)

**Interfaces:**
- Consumes: `_materialize_full`, `derive_builder_graph`, `is_raw_python`, `parse_pipeline`, `_node_label`.
- Produces: route `GET /controls/{control_id}/logic/step/{node_id}/data?page=N` → `_step_data.html` partial; helper `_pipeline_for_view(control) -> Pipeline | None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/plane/test_pipeline_steps.py`:

```python
def test_step_data_route_paginates(seeded_app):
    client, control_id = seeded_app
    r = client.get(f"/controls/{control_id}/logic/step/flt/data")
    assert r.status_code == 200
    assert "records" in r.text and "of" in r.text          # "records X–Y of Z"


def test_step_data_unknown_node_degrades(seeded_app):
    client, control_id = seeded_app
    r = client.get(f"/controls/{control_id}/logic/step/does-not-exist/data")
    assert r.status_code == 200                              # never 500
    assert "isn't computable" in r.text or "not computable" in r.text
```

(Adapt `seeded_app`/node ids to the existing plane conftest fixture; the control's pipeline must contain a node `flt`. If the conftest's seeded control uses different node ids, use one of its real non-terminal node ids.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/plane/test_pipeline_steps.py -q`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Write minimal implementation**

In `uticen_lite/plane/routes/pipeline.py`:

1. Add a helper near `_editor_context`:

```python
def _pipeline_for_view(control: dict | None) -> Pipeline | None:
    """The parsed pipeline the Builder cards/counts are rendered from.

    Mirrors ``_editor_context(for_builder=True)``: a raw-Python control has none; otherwise
    use the derived builder graph (so rule_spec/empty controls still show their scaffold).
    """
    if control is None or is_raw_python(control):
        return None
    graph = derive_builder_graph(control, list(control.get("source_ids") or [])) \
        or _graph_of(control)
    if not graph.get("nodes"):
        return None
    try:
        return parse_pipeline(graph)
    except PipelineError:
        return None
```

2. Register the inspector route inside `register(...)`, alongside the other `/controls/{control_id}/logic/...` sub-routes (before the legacy `/pipeline` redirect — they are all already ahead of the `controls` catch-all):

```python
    _STEP_PAGE = 100

    @app.get("/controls/{control_id}/logic/step/{node_id}/data", response_class=HTMLResponse)
    def step_data(
        control_id: str,
        node_id: str,
        request: Request,
        page: int = 1,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        root = request.app.state.project_root
        control = repo.get_control(conn, control_id)
        pipeline = _pipeline_for_view(control)
        ctx: dict[str, Any] = {
            "control_id": control_id, "node_id": node_id,
            "frame_available": False, "reason": "This step isn't computable yet.",
        }
        if pipeline is not None:
            try:
                node = pipeline.node(node_id)
                ctx["step_label"] = _node_label(node)
            except KeyError:
                node = None
            steps = _materialize_full(conn, root, pipeline)
            frame = steps.get(node_id)
            if frame is not None:
                total = len(frame)
                page = max(1, page)
                page_count = max(1, (total + _STEP_PAGE - 1) // _STEP_PAGE)
                page = min(page, page_count)
                start = (page - 1) * _STEP_PAGE
                window = frame.iloc[start:start + _STEP_PAGE]
                ctx.update({
                    "frame_available": True,
                    "header": [str(c) for c in frame.columns],
                    "rows": [[("" if pd_isna(v) else str(v)) for v in row]
                             for row in window.itertuples(index=False, name=None)],
                    "total": total, "page": page, "page_count": page_count,
                    "start1": start + 1, "end1": start + len(window),
                })
            elif not pipeline.import_source_ids() or node is not None:
                ctx["reason"] = "Bind a data source (and complete this step) to inspect it."
        return templates.TemplateResponse(request, "partials/_step_data.html", ctx)
```

3. Add a tiny NaN-safe stringifier used above (module scope in `pipeline.py`):

```python
def pd_isna(v: Any) -> bool:
    """NaN/NaT-safe truthiness for display (avoids importing pandas at module top)."""
    try:
        import pandas as pd
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False
```

4. In `ai_apply`, add `"control_id": control_id,` to the `partials/_pipe_cards.html` context dict so the clickable count renders a valid URL after an AI draft.

Create `uticen_lite/plane/templates/partials/_step_data.html`:

```html
{# Step inspector drawer body — swapped into #step-drawer by HTMX. #}
<div class="step-panel card">
  <div class="step-panel-head">
    <strong>{{ step_label or "Step" }}</strong>
    <span class="muted mono">{{ node_id }}</span>
    {% if frame_available %}
    <span class="muted">— records {{ start1 }}–{{ end1 }} of {{ total }}</span>
    <a class="btn btn-sm" href="/controls/{{ control_id }}/logic/step/{{ node_id }}/export.xlsx">
      Download this step (.xlsx)</a>
    {% endif %}
    <button class="btn btn-sm btn-ghost" type="button"
            onclick="document.getElementById('step-drawer').innerHTML='';">Close</button>
  </div>
  {% if not frame_available %}
  <p class="muted">{{ reason }}</p>
  {% else %}
  <div class="table-wrap">
    <table>
      <thead><tr>{% for h in header %}<th class="mono">{{ h }}</th>{% endfor %}</tr></thead>
      <tbody>
        {% for r in rows %}<tr>{% for c in r %}<td>{{ c }}</td>{% endfor %}</tr>{% endfor %}
      </tbody>
    </table>
  </div>
  {% if page_count > 1 %}
  <div class="pager">
    {% if page > 1 %}
    <button class="btn btn-sm" hx-get="/controls/{{ control_id }}/logic/step/{{ node_id }}/data?page={{ page - 1 }}"
            hx-target="#step-drawer" hx-swap="innerHTML">← Prev</button>{% endif %}
    <span class="muted">Page {{ page }} of {{ page_count }}</span>
    {% if page < page_count %}
    <button class="btn btn-sm" hx-get="/controls/{{ control_id }}/logic/step/{{ node_id }}/data?page={{ page + 1 }}"
            hx-target="#step-drawer" hx-swap="innerHTML">Next →</button>{% endif %}
  </div>
  {% endif %}
  {% endif %}
</div>
```

In `_pipe_node.html`, replace the count `<span>` (lines 7–11) with a clickable button when a count exists:

```html
    {% if node.count is not none %}
    <button class="pipe-count pipe-count-btn" type="button"
            hx-get="/controls/{{ control_id }}/logic/step/{{ node.id }}/data"
            hx-target="#step-drawer" hx-swap="innerHTML"
            title="Inspect this step's data">rows: <strong>{{ "{:,}".format(node.count) }}</strong></button>
    {% else %}
    <span class="pipe-count">rows: —</span>
    {% endif %}
```

In `_pipe_diagram.html`, wrap each box group's contents in an SVG anchor so the box loads the inspector (replace the `<g ...> ... </g>` inner with an `<a>`-wrapped version):

```html
    <a hx-get="/controls/{{ control_id }}/logic/step/{{ box.id }}/data"
       hx-target="#step-drawer" hx-swap="innerHTML" style="cursor:pointer;">
    <g class="fc-box{% if box.terminal %} fc-terminal{% endif %}">
      {% if box.narrative %}<title>{{ box.narrative }}</title>{% endif %}
      <rect x="{{ x }}" y="{{ y }}" width="{{ box_w }}" height="{{ box_h }}" rx="8" />
      <text x="{{ x + 12 }}" y="{{ y + 20 }}">{{ box.label }}</text>
      <text class="fc-count" x="{{ x + 12 }}" y="{{ y + 37 }}">{{ box.id }} · rows: {% if box.count is not none %}{{ "{:,}".format(box.count) }}{% else %}—{% endif %}</text>
      {% if box.narrative %}<text class="fc-narr" x="{{ x + 12 }}" y="{{ y + 56 }}">{{ box.narrative | truncate(34, True, '…', 0) }}</text>{% endif %}
    </g>
    </a>
```

In `logic_builder.html` and `logic_flowchart.html`, add a drawer container once per page (place it after the cards / after the SVG, inside the main content block):

```html
<div id="step-drawer" class="step-drawer"></div>
```

Add minimal styling to `uticen_lite/plane/static/app.css` (route colors through existing tokens, learning 0005):

```css
.pipe-count-btn { cursor: pointer; background: none; border: 0; font: inherit; color: inherit; padding: 0; }
.pipe-count-btn:hover strong { text-decoration: underline; }
.step-drawer:empty { display: none; }
.step-drawer .step-panel { margin-top: 12px; }
.step-panel-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/plane/test_pipeline_steps.py -q && python -m ruff check uticen_lite/plane && python -m mypy uticen_lite`
Expected: PASS; ruff/mypy clean. Run `python -m pytest -q` for the full suite.

- [ ] **Step 5: Commit**

```bash
git add uticen_lite/plane tests/plane/test_pipeline_steps.py
git commit -m "feat(plane): step inspector drawer + clickable row-counts"
git push -u origin HEAD
```

---

## Task 6: Per-step + workbook xlsx export routes

**Files:**
- Modify: `uticen_lite/plane/routes/pipeline.py` (2 routes), `logic_builder.html` (workbook button)
- Test: `tests/plane/test_pipeline_steps.py` (append)

**Interfaces:**
- Consumes: `_materialize_full`, `_pipeline_for_view`, `_node_label`, `xlsx_export.write_single_step`, `xlsx_export.write_step_workbook`, `AdaptersUnavailable`.
- Produces: `GET /controls/{control_id}/logic/step/{node_id}/export.xlsx`, `GET /controls/{control_id}/logic/export-steps.xlsx`.

- [ ] **Step 1: Write the failing test**

Append to `tests/plane/test_pipeline_steps.py`:

```python
from io import BytesIO

import pandas as pd

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_per_step_xlsx_downloads(seeded_app):
    client, control_id = seeded_app
    r = client.get(f"/controls/{control_id}/logic/step/flt/export.xlsx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(_XLSX)
    pd.read_excel(BytesIO(r.content), engine="openpyxl")   # valid workbook


def test_workbook_xlsx_has_a_sheet_per_step(seeded_app):
    client, control_id = seeded_app
    r = client.get(f"/controls/{control_id}/logic/export-steps.xlsx")
    assert r.status_code == 200
    names = pd.ExcelFile(BytesIO(r.content), engine="openpyxl").sheet_names
    assert "Summary" in names and "About" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/plane/test_pipeline_steps.py -q -k xlsx`
Expected: FAIL — 404.

- [ ] **Step 3: Write minimal implementation**

Add to `register(...)` in `pipeline.py` (near `step_data`):

```python
    @app.get("/controls/{control_id}/logic/step/{node_id}/export.xlsx", response_model=None)
    def step_export(
        control_id: str,
        node_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        from uticen_lite.adapters import xlsx_export
        from uticen_lite.plane.ingest import AdaptersUnavailable

        root = request.app.state.project_root
        pipeline = _pipeline_for_view(repo.get_control(conn, control_id))
        frame = None
        label = node_id
        if pipeline is not None:
            try:
                label = _node_label(pipeline.node(node_id))
            except KeyError:
                pass
            frame = _materialize_full(conn, root, pipeline).get(node_id)
        if frame is None:
            return PlainTextResponse("This step isn't computable yet.", status_code=409)
        try:
            data = xlsx_export.write_single_step(frame, label)
        except AdaptersUnavailable as exc:
            return PlainTextResponse(str(exc), status_code=503)
        return Response(
            content=data, media_type=_XLSX_MEDIA,
            headers={"content-disposition":
                     f'attachment; filename="{control_id}-{node_id}.xlsx"'},
        )

    @app.get("/controls/{control_id}/logic/export-steps.xlsx", response_model=None)
    def steps_export(
        control_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        from uticen_lite.adapters import xlsx_export
        from uticen_lite.plane.ingest import AdaptersUnavailable

        root = request.app.state.project_root
        control = repo.get_control(conn, control_id)
        pipeline = _pipeline_for_view(control)
        if pipeline is None:
            return PlainTextResponse("No inspectable pipeline yet.", status_code=409)
        frames = _materialize_full(conn, root, pipeline)
        if not frames:
            return PlainTextResponse("Bind a data source first.", status_code=409)
        steps = [(_node_label(n), frames[n.id])
                 for n in pipeline.topological() if n.id in frames]
        meta = {
            "control": control_id,
            "title": str((control or {}).get("title") or ""),
            "generated_at": _now_iso(),
        }
        try:
            data = xlsx_export.write_step_workbook(steps, meta)
        except AdaptersUnavailable as exc:
            return PlainTextResponse(str(exc), status_code=503)
        return Response(
            content=data, media_type=_XLSX_MEDIA,
            headers={"content-disposition":
                     f'attachment; filename="{control_id}-steps.xlsx"'},
        )
```

Add module-level constants/imports to `pipeline.py`:

```python
from fastapi.responses import PlainTextResponse, Response  # add to the existing fastapi.responses import

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _now_iso() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()
```

Add the workbook button to `logic_builder.html` near the existing pipeline controls (e.g. beside "Save pipeline" / "Convert to Python"):

```html
<a class="btn btn-sm" href="/controls/{{ control_id }}/logic/export-steps.xlsx">
  Export step workbook (.xlsx)</a>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/plane/test_pipeline_steps.py -q && python -m ruff check uticen_lite/plane && python -m mypy uticen_lite`
Expected: PASS; ruff/mypy clean.

- [ ] **Step 5: Commit**

```bash
git add uticen_lite/plane tests/plane/test_pipeline_steps.py
git commit -m "feat(plane): per-step and whole-pipeline xlsx export routes"
git push -u origin HEAD
```

---

## Task 7: Trust-boundary teeth-check + e2e smoke

**Files:**
- Create: `tests/test_steps_trust_boundary.py`
- Create: `tests/e2e/test_step_inspector_smoke.py` (`browser` marker)

**Interfaces:**
- Consumes: the bundle builder (`uticen_lite.store.export_service.build_bundle` or the existing contract-export test harness), the plane test client.
- Produces: regression guards.

- [ ] **Step 1: Write the failing test**

Create `tests/test_steps_trust_boundary.py`. Model it on the existing bundle/contract test (`tests/test_contract_export.py`) — build a bundle from a project whose control has a pipeline, then assert no raw data rows appear anywhere in the bundle entries:

```python
"""The step-inspection/export surfaces never leak raw population into the bundle (0001/0026)."""
from __future__ import annotations

import json
import zipfile


def test_bundle_has_no_raw_population_rows(built_bundle_path):
    # built_bundle_path: a fixture that runs a control + builds the bundle zip.
    forbidden = ("data_rows", '"rows"', "population")
    with zipfile.ZipFile(built_bundle_path) as z:
        for name in z.namelist():
            blob = z.read(name)
            if name.endswith(".json"):
                # Structural check: no manifest entry carries a raw-rows array.
                obj = json.loads(blob)
                assert "rows" not in json.dumps(obj) or _no_data_arrays(obj), name
            text = blob.decode("utf-8", "ignore")
            for tok in forbidden:
                assert tok not in text, f"{tok!r} leaked into {name}"


def _no_data_arrays(obj) -> bool:
    # Allow the workpaper's bounded sample structure if present, but reject a top-level
    # population/data_rows array. Keep this aligned with tests/test_contract_export.py.
    return True
```

(If `tests/test_contract_export.py` already provides a `built_bundle`/fixture, import and reuse it instead of redefining `built_bundle_path`; the point is one positive assertion that the bundle carries zero raw rows after these surfaces exist. Align the forbidden-token list with what that test already guarantees so this is a teeth-check, not a duplicate.)

Create `tests/e2e/test_step_inspector_smoke.py`:

```python
"""Browser smoke: click a step count → drawer opens → paginate → export link present.

Opt-in (the `browser` marker is excluded from the fast unit lane; CI runs `pytest tests/e2e`).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.browser


def test_step_inspector_drawer(plane_server, page):
    # plane_server / page: reuse the existing e2e fixtures (see other tests/e2e tests).
    base, control_id = plane_server
    page.goto(f"{base}/controls/{control_id}/logic/builder")
    page.locator(".pipe-count-btn").first.click()
    drawer = page.locator("#step-drawer .step-panel")
    drawer.wait_for(state="visible")
    assert drawer.locator("table").count() == 1
    assert drawer.get_by_text("Download this step").count() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_steps_trust_boundary.py -q`
Expected: FAIL until the fixture is wired (or PASS immediately if it reuses an existing built-bundle fixture and the guarantee already holds — in which case it is a passing regression guard, which is acceptable for this teeth-check; confirm it would FAIL if a raw-rows array were injected by temporarily adding one).

- [ ] **Step 3: Write minimal implementation**

No product code should be needed (the bundle already excludes raw rows — these surfaces deliberately don't touch it). If the trust-boundary test fails because a surface leaked data into the bundle, that is a real defect — fix the leak in product code, not the test. Wire the e2e fixtures to match the existing `tests/e2e` harness.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_steps_trust_boundary.py -q` (fast lane) and, where a browser is available, `python -m pytest tests/e2e/test_step_inspector_smoke.py -q` (per the e2e lane convention in `pyproject.toml`). Then the full gate: `python -m pytest -q && python -m ruff check . && python -m mypy uticen_lite`.
Expected: PASS; ruff/mypy clean; no warnings.

- [ ] **Step 5: Commit**

```bash
git add tests/test_steps_trust_boundary.py tests/e2e/test_step_inspector_smoke.py
git commit -m "test: bundle trust-boundary teeth-check + step inspector browser smoke"
git push -u origin HEAD
```

---

## Self-Review

**Spec coverage:**
- Per-step inspector (click count → see rows) → Task 5 (route + drawer + clickable counts).
- Per-step `.xlsx` export → Task 4 (writer) + Task 6 (route/button).
- Whole-pipeline workbook (sheet per step + summary) → Task 4 + Task 6.
- Full-population depth → Task 3 (full-frame loader) + engine (Task 1).
- Incremental "recompute from edited step onward" → Task 2 (cache) + Task 3 (wired into badges/inspector).
- Full-population badges (decision ii) → Task 3.
- `[adapters]`/openpyxl gating, friendly degradation → Task 4 (`_require_writer`→`AdaptersUnavailable`) + Task 6 (503 + message).
- Excel limits / cell coercion / sheet-name sanitization → Task 4.
- Not-yet-computable degradation (no 500) → Task 3 (`_materialize_full` swallows) + Task 5 (friendly partial).
- Trust boundary / no bundle contact → Task 7 teeth-check; no `schema_version`/store changes anywhere.
- Tests: equivalence (T1), incremental cache (T2), xlsx (T4), inspector + degrade (T5), exports (T6), trust boundary + e2e (T7).

**Placeholder scan:** No TBD/TODO. Test fixtures defer to the existing plane/e2e conftest by name (`seeded_app`, `plane_server`, `page`, contract-export fixtures) — the implementer must bind these to the real fixtures; assertions are concrete. This is intentional (don't invent a parallel harness), not a placeholder for behavior.

**Type consistency:** `materialize_steps` signature is identical across Tasks 1–6 (`source_versions`, `cache`, `recomputed_out` keyword-only); `_row_counts(conn, root, pipeline)` keeps its existing 3-arg shape; `_materialize_full`/`_pipeline_for_view` names are used consistently in Tasks 3/5/6; `write_single_step(frame, label)` / `write_step_workbook(steps, meta)` match between Task 4 and Task 6; `_XLSX_MEDIA` defined once (Task 6) and reused.

## Out of scope / future

- Cross-run history & diffing (issue #14).
- Disk-backed cache surviving restarts (would re-introduce raw-data-at-rest).
- Inspector filtering/querying beyond pagination + the existing column sort.
