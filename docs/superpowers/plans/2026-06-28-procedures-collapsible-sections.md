# Collapsible Procedure Sections on the Logic Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the Logic page so node cards group under a shared **Inputs** band + one **collapsible
section per procedure** (the section header *is* the procedure editor), with matching procedure swimlanes
in the read-only Flowchart.

**Architecture:** One new pure helper, `group_nodes_by_band(pipeline)`, partitions node ids into a shared
band + per-procedure bands; both the Builder route context and the Flowchart `_diagram` consume it. The
Builder template renders the bands as `<details>` sections; the Flowchart renders them as swimlane bands
with server-rendered collapse. **No procedure-model, run/rollup, workpaper, store-schema, or
bundle/contract change** — this is `plane/` UI + the one helper.

**Tech Stack:** Python ≥3.11, FastAPI + HTMX + Jinja2, vanilla JS, native `<details>`/`<summary>`,
`localStorage`, pytest, Playwright (`tests/e2e -m browser`), ruff (py311, line-length 100), mypy.

## EXECUTION RULES

- Never ask the user for permission to continue between tasks. Execute the full plan start to finish.
- On an unresolvable error after 2–3 attempts: note it in the ledger and skip to the next task.
- After every `git commit`, push:
  ```bash
  git push -u origin HEAD
  ```
- Keep the suite green (`python -m pytest -q`), `ruff check .`, and `mypy uticen_lite` clean after every task.

## Global Constraints

- **Cardinal rule (learning 0001):** nothing bundle-facing changes. `contract/bundle.schema.json`,
  `uticen_lite/schema/bundle.schema.json`, `bundle/`, and `model/workpaper.py` MUST NOT appear in this
  branch's diff. No `schema_version` bump. No store-schema migration (`store/migrations.py` unchanged).
- **Pyodide-safe core (STRATEGY.md):** `pipeline/procedures.py` stays pure — no `import pandas`, no store,
  no render. The grouping helper operates only on the `Pipeline` graph.
- **Never 500 (learnings 0013/0033):** every new route/template path degrades gracefully on an
  incomplete/unparsable graph — an unparsable graph renders all cards in the Inputs band; collapse params
  with unknown ids are ignored; no new raise paths.
- **Server-rendered collapse (learning 0007):** Flowchart collapse is a `GET ?collapsed=<ids>` re-render,
  not client-side SVG surgery.
- **Effective procedures own labels (learning 0036):** a *sole* auto-derived procedure keeps `code=""`;
  the section header falls back to a neutral label, never a bare "P1".
- **e2e is load-bearing (learning 0012):** re-run + re-derive `pytest tests/e2e -m browser` for the
  restructured Builder DOM and the new collapse/insert semantics.
- ruff target `py311`, line-length 100; mypy clean; pytest output pristine (no stray warnings).

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `uticen_lite/pipeline/procedures.py` | **New** `group_nodes_by_band(pipeline)` pure helper. | 1 |
| `tests/pipeline/test_procedures_bands.py` | **New** unit tests for the helper. | 1 |
| `uticen_lite/plane/routes/pipeline.py` | `_card_bands()` builder; thread `bands` through every `_pipe_cards.html` render site; `_diagram(..., collapsed)`; flowchart `?collapsed=`. | 2, 4 |
| `tests/plane/test_logic_bands.py` | **New** route-context tests for `bands`. | 2 |
| `uticen_lite/plane/templates/partials/_pipe_cards.html` | Render Inputs band + `<details>` procedure sections + section-scoped insert zones + "＋ Add procedure". | 3 |
| `uticen_lite/plane/templates/partials/_procedures_panel.html` | **Deleted** (absorbed into section headers). | 3 |
| `uticen_lite/plane/templates/partials/_pipe_node.html` | Relabel "Procedure"→"Belongs to"; drop redundant Test-card chips; chips only on shared cards. | 3 |
| `uticen_lite/plane/templates/logic_builder.html` | Remove panel include; JS: serialize from section headers, insert-in-section, belongs-to-move autosave, `<details>` localStorage, add/delete section. | 3 |
| `uticen_lite/plane/templates/partials/_pipe_diagram.html` | Swimlane band backgrounds/labels + collapse toggles + summary boxes. | 4 |
| `uticen_lite/plane/templates/logic_flowchart.html` | HTMX-swappable flowchart container reading `?collapsed=`. | 4 |
| `uticen_lite/plane/static/app.css` | Section/band/summary styling; retire `.proc-panel`/`.proc-row`/`#proc-add`. | 3, 4 |
| `tests/e2e/test_smoke.py` / `test_multi_procedure.py` | Extend for sectioned Builder, collapse, insert-in-section, flowchart collapse. | 5 |

---

### Task 1: `group_nodes_by_band` helper

**Files:**
- Modify: `uticen_lite/pipeline/procedures.py` (append a function; imports already present)
- Test: `tests/pipeline/test_procedures_bands.py` (create)

**Interfaces:**
- Consumes: `Pipeline`, `effective_procedures(pipeline)`, `derived_membership(pipeline)` (all already in
  `uticen_lite/pipeline/procedures.py`). `Pipeline.topological()` returns `list[Node]`; `Node` has
  `.id: str` and `.type: str`.
- Produces: `group_nodes_by_band(pipeline: Pipeline) -> dict[str, Any]` returning
  `{"shared": list[str], "procedures": [{"id": str, "node_ids": list[str]}, ...]}`. `procedures` is
  ordered by effective-procedure position; every node id appears in exactly one band; `import` nodes are
  always in `shared`; a non-import node private to exactly one procedure goes in that procedure's band;
  everything else (membership 0 or ≥2) goes in `shared`.

- [ ] **Step 1: Write the failing tests**

Create `tests/pipeline/test_procedures_bands.py`:

```python
"""Unit tests for group_nodes_by_band — the Inputs/per-procedure partition that
both the Builder and the Flowchart consume."""

from __future__ import annotations

from uticen_lite.pipeline.model import parse_pipeline
from uticen_lite.pipeline.procedures import effective_procedures, group_nodes_by_band


def _topo_index(pipeline):
    return {n.id: i for i, n in enumerate(pipeline.topological())}


def test_shared_import_and_private_branches():
    # src feeds two tests in two procedures → src shared, each test private.
    pipe = parse_pipeline({
        "nodes": [
            {"id": "src", "type": "import", "source_id": "s"},
            {"id": "t1", "type": "test", "inputs": ["src"],
             "config": {"procedure_id": "p1", "conditions": [{"column": "a", "op": "not_empty"}]}},
            {"id": "t2", "type": "test", "inputs": ["src"],
             "config": {"procedure_id": "p2", "conditions": [{"column": "a", "op": "not_empty"}]}},
        ],
        "procedures": [
            {"id": "p1", "code": "P1", "name": "One", "position": 0},
            {"id": "p2", "code": "P2", "name": "Two", "position": 1},
        ],
    })
    bands = group_nodes_by_band(pipe)
    assert bands["shared"] == ["src"]
    assert bands["procedures"] == [
        {"id": "p1", "node_ids": ["t1"]},
        {"id": "p2", "node_ids": ["t2"]},
    ]


def test_shared_filter_stays_shared_private_filter_nests():
    # src → sf (shared filter) → f1 → t1(p1); sf → t2(p2).
    pipe = parse_pipeline({
        "nodes": [
            {"id": "src", "type": "import", "source_id": "s"},
            {"id": "sf", "type": "filter", "inputs": ["src"],
             "config": {"conditions": [{"column": "a", "op": "not_empty"}]}},
            {"id": "f1", "type": "filter", "inputs": ["sf"],
             "config": {"conditions": [{"column": "b", "op": "not_empty"}]}},
            {"id": "t1", "type": "test", "inputs": ["f1"],
             "config": {"procedure_id": "p1", "conditions": [{"column": "a", "op": "not_empty"}]}},
            {"id": "t2", "type": "test", "inputs": ["sf"],
             "config": {"procedure_id": "p2", "conditions": [{"column": "a", "op": "not_empty"}]}},
        ],
        "procedures": [
            {"id": "p1", "code": "P1", "name": "One", "position": 0},
            {"id": "p2", "code": "P2", "name": "Two", "position": 1},
        ],
    })
    bands = group_nodes_by_band(pipe)
    assert bands["shared"] == ["src", "sf"]
    assert bands["procedures"] == [
        {"id": "p1", "node_ids": ["f1", "t1"]},
        {"id": "p2", "node_ids": ["t2"]},
    ]


def test_flattened_band_order_is_topologically_valid():
    pipe = parse_pipeline({
        "nodes": [
            {"id": "src", "type": "import", "source_id": "s"},
            {"id": "sf", "type": "filter", "inputs": ["src"],
             "config": {"conditions": [{"column": "a", "op": "not_empty"}]}},
            {"id": "f1", "type": "filter", "inputs": ["sf"],
             "config": {"conditions": [{"column": "b", "op": "not_empty"}]}},
            {"id": "t1", "type": "test", "inputs": ["f1"],
             "config": {"procedure_id": "p1", "conditions": [{"column": "a", "op": "not_empty"}]}},
            {"id": "t2", "type": "test", "inputs": ["sf"],
             "config": {"procedure_id": "p2", "conditions": [{"column": "a", "op": "not_empty"}]}},
        ],
        "procedures": [
            {"id": "p1", "code": "P1", "name": "One", "position": 0},
            {"id": "p2", "code": "P2", "name": "Two", "position": 1},
        ],
    })
    bands = group_nodes_by_band(pipe)
    flat = bands["shared"] + [nid for b in bands["procedures"] for nid in b["node_ids"]]
    # Every node appears once.
    assert sorted(flat) == sorted(n.id for n in pipe.nodes)
    # A node never precedes one of its inputs in the flattened band order.
    idx = {nid: i for i, nid in enumerate(flat)}
    for n in pipe.nodes:
        for src in n.inputs:
            assert idx[src] < idx[n.id]


def test_orphan_and_no_procedures_fallback():
    # No defined procedures, single import→test: one auto procedure; import shared.
    pipe = parse_pipeline({
        "nodes": [
            {"id": "src", "type": "import", "source_id": "s"},
            {"id": "t", "type": "test", "inputs": ["src"],
             "config": {"conditions": [{"column": "a", "op": "not_empty"}]}},
        ],
    })
    bands = group_nodes_by_band(pipe)
    auto_id = effective_procedures(pipe)[0].id
    assert bands["shared"] == ["src"]
    assert bands["procedures"] == [{"id": auto_id, "node_ids": ["t"]}]


def test_empty_defined_procedure_keeps_its_band():
    pipe = parse_pipeline({
        "nodes": [
            {"id": "src", "type": "import", "source_id": "s"},
            {"id": "t1", "type": "test", "inputs": ["src"],
             "config": {"procedure_id": "p1", "conditions": [{"column": "a", "op": "not_empty"}]}},
        ],
        "procedures": [
            {"id": "p1", "code": "P1", "name": "One", "position": 0},
            {"id": "p2", "code": "P2", "name": "Empty", "position": 1},
        ],
    })
    bands = group_nodes_by_band(pipe)
    assert bands["procedures"] == [
        {"id": "p1", "node_ids": ["t1"]},
        {"id": "p2", "node_ids": []},
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/pipeline/test_procedures_bands.py -q`
Expected: FAIL with `ImportError: cannot import name 'group_nodes_by_band'`.

- [ ] **Step 3: Implement the helper**

Append to `uticen_lite/pipeline/procedures.py` (the module already imports `Node, Pipeline,
ProcedureDef` and `from __future__ import annotations`; add `from typing import Any` to the imports at
the top of the file):

```python
def group_nodes_by_band(pipeline: Pipeline) -> dict[str, Any]:
    """Partition node ids into a shared "Inputs" band + one band per effective procedure.

    - ``import`` nodes always sit in the shared band (the data the author brings in).
    - A non-import node belonging to exactly ONE procedure (derived membership ``{P}``)
      sits in ``P``'s band — the nodes private to that procedure's branch.
    - Everything else (membership 0 — orphan/unassigned — or ≥2 — shared upstream
      steps) sits in the shared band.

    Bands preserve topological order within each band, and ``procedures`` is ordered by
    effective-procedure position. The flattened order ``shared + each procedure's nodes``
    is always a valid topological order (a node private to one procedure can never depend
    on a node private to another — that node would be shared). Pure / pandas-free.
    """
    eff = effective_procedures(pipeline)
    by_proc: dict[str, list[str]] = {p.id: [] for p in eff}
    membership = derived_membership(pipeline)
    shared: list[str] = []
    for node in pipeline.topological():
        pids = membership.get(node.id, set())
        if node.type != "import" and len(pids) == 1:
            (only,) = tuple(pids)
            (by_proc[only] if only in by_proc else shared).append(node.id)
        else:
            shared.append(node.id)
    return {
        "shared": shared,
        "procedures": [{"id": p.id, "node_ids": by_proc[p.id]} for p in eff],
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/pipeline/test_procedures_bands.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Lint, type-check, commit, push**

```bash
python -m ruff check uticen_lite tests && python -m mypy uticen_lite
git add uticen_lite/pipeline/procedures.py tests/pipeline/test_procedures_bands.py
git commit -m "feat: group_nodes_by_band — Inputs/per-procedure node partition"
git push -u origin HEAD
```

---

### Task 2: Builder route context — `bands` everywhere `_pipe_cards.html` renders

**Files:**
- Modify: `uticen_lite/plane/routes/pipeline.py`
- Test: `tests/plane/test_logic_bands.py` (create)

**Interfaces:**
- Consumes: `group_nodes_by_band` (Task 1); existing `_procedure_context(pipeline)` → `{"procedures":
  [{id, code, name, assertion, failure_threshold_pct, failure_threshold_count, color}], "node_procedures":
  {...}, "selected_procedure_for": {...}}`; existing `_card_vm`/`_raw_card_vm` node view-models (each a dict
  with an `id` key).
- Produces: `_card_bands(cards_pipeline, node_vms, proc_ctx) -> dict[str, Any]` returning
  `{"shared": {"key": "__inputs__", "nodes": [vm, ...]}, "procedures": [{"key": pid, "proc": {…proc vm…},
  "nodes": [vm, ...]}, ...]}`. Every render site that returns `partials/_pipe_cards.html` puts this under
  the context key `bands`. When `cards_pipeline is None` (unparsable graph): `{"shared": {"key":
  "__inputs__", "nodes": node_vms}, "procedures": []}`.

**Background:** `partials/_pipe_cards.html` is rendered from FOUR places — find them all first:
`grep -n "_pipe_cards.html" uticen_lite/plane/routes/pipeline.py` → the GET full page goes through
`_editor_context` (`logic_builder.html` includes the partial), and three POST sites return the partial
directly: `save_pipeline` autosave-success (~line 1070), `save_pipeline` autosave-error 422 (~line 1020),
and the AI-apply handler (`grep -n "_pipe_cards.html"` will also surface it). **All four must provide
`bands`.** Keep the existing `nodes` key in every context too (Task 3 stops using it, but keeping it avoids
breaking the suite between tasks).

- [ ] **Step 1: Write the failing test**

Create `tests/plane/test_logic_bands.py`:

```python
"""The Builder route context exposes `bands`: a shared Inputs band + per-procedure
bands, each carrying the node view-models that belong in it."""

from __future__ import annotations

from uticen_lite.pipeline.model import parse_pipeline
from uticen_lite.plane.routes.pipeline import _card_bands, _card_vm, _procedure_context


def _vms(pipeline):
    return [_card_vm(n, pipeline, {}, {}, {}) for n in pipeline.topological()]


def test_card_bands_groups_vms_by_procedure():
    pipe = parse_pipeline({
        "nodes": [
            {"id": "src", "type": "import", "source_id": "s"},
            {"id": "t1", "type": "test", "inputs": ["src"],
             "config": {"procedure_id": "p1", "conditions": [{"column": "a", "op": "not_empty"}]}},
            {"id": "t2", "type": "test", "inputs": ["src"],
             "config": {"procedure_id": "p2", "conditions": [{"column": "a", "op": "not_empty"}]}},
        ],
        "procedures": [
            {"id": "p1", "code": "P1", "name": "One", "position": 0},
            {"id": "p2", "code": "P2", "name": "Two", "position": 1},
        ],
    })
    bands = _card_bands(pipe, _vms(pipe), _procedure_context(pipe))
    assert bands["shared"]["key"] == "__inputs__"
    assert [vm["id"] for vm in bands["shared"]["nodes"]] == ["src"]
    assert [b["key"] for b in bands["procedures"]] == ["p1", "p2"]
    assert [vm["id"] for vm in bands["procedures"][0]["nodes"]] == ["t1"]
    assert bands["procedures"][0]["proc"]["code"] == "P1"


def test_card_bands_unparsable_pipeline_all_shared():
    vms = [{"id": "x"}, {"id": "y"}]
    bands = _card_bands(None, vms, {"procedures": [], "node_procedures": {}, "selected_procedure_for": {}})
    assert bands["procedures"] == []
    assert [vm["id"] for vm in bands["shared"]["nodes"]] == ["x", "y"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/plane/test_logic_bands.py -q`
Expected: FAIL with `ImportError: cannot import name '_card_bands'`.

- [ ] **Step 3: Add `_card_bands` to `routes/pipeline.py`**

Add this helper just below `_procedure_context` (after line ~577). It is best-effort — any failure yields
a single Inputs band (never 500, learning 0013):

```python
def _card_bands(
    cards_pipeline: Pipeline | None,
    node_vms: list[dict[str, Any]],
    proc_ctx: dict[str, Any],
) -> dict[str, Any]:
    """Group the ordered node view-models into a shared Inputs band + per-procedure
    bands for the sectioned Builder. Best-effort: an unparsable graph (or any failure)
    puts every card in the Inputs band so the page still renders (learning 0013)."""
    vm_by_id = {vm["id"]: vm for vm in node_vms}
    fallback = {"shared": {"key": "__inputs__", "nodes": node_vms}, "procedures": []}
    if cards_pipeline is None:
        return fallback
    try:
        from uticen_lite.pipeline.procedures import group_nodes_by_band

        grouped = group_nodes_by_band(cards_pipeline)
        proc_by_id = {p["id"]: p for p in proc_ctx.get("procedures", [])}
        procedures = [
            {
                "key": band["id"],
                "proc": proc_by_id.get(band["id"], {"id": band["id"], "code": "", "name": "",
                                                    "assertion": "", "failure_threshold_pct": None,
                                                    "failure_threshold_count": None, "color": "#888"}),
                "nodes": [vm_by_id[nid] for nid in band["node_ids"] if nid in vm_by_id],
            }
            for band in grouped["procedures"]
        ]
        shared_nodes = [vm_by_id[nid] for nid in grouped["shared"] if nid in vm_by_id]
        return {"shared": {"key": "__inputs__", "nodes": shared_nodes}, "procedures": procedures}
    except Exception:  # noqa: BLE001 — incomplete graph → one Inputs band, never 500 (0013)
        return fallback
```

- [ ] **Step 4: Thread `bands` into `_editor_context`**

In `_editor_context` (the `return {...}` at line ~667), add the `bands` key. Both branches above it already
compute `ordered_nodes` and `cards_pipeline`; build `bands` from them just before the return:

```python
    proc_ctx = _procedure_context(cards_pipeline)
    ...
    return {
        # Procedure panel + per-Test selector + derived chips (best-effort; 0013).
        **proc_ctx,
        ...
        "nodes": ordered_nodes,
        "bands": _card_bands(cards_pipeline, ordered_nodes, proc_ctx),
        ...
    }
```

Replace the existing `**_procedure_context(cards_pipeline),` line with the `proc_ctx` variable (compute
`proc_ctx = _procedure_context(cards_pipeline)` once, spread `**proc_ctx`, and pass it to `_card_bands`).

- [ ] **Step 5: Thread `bands` into the three POST render sites**

In `save_pipeline` autosave-success (~line 1070), autosave-error 422 (~line 1020), and the AI-apply handler:
each builds a context dict for `partials/_pipe_cards.html` containing `nodes`, `sources`, `op_choices`,
`join_mode_choices`, and `**_procedure_context(parsed)`. For each, capture the procedure context in a
variable and add `bands`:

```python
                proc_ctx = _procedure_context(err_parsed)   # or builder_parsed / the AI handler's parsed
                return templates.TemplateResponse(
                    request,
                    "partials/_pipe_cards.html",
                    {
                        "control_id": control_id,
                        "nodes": err_nodes,           # or ordered_nodes
                        "sources": sources,
                        "op_choices": OP_CHOICES,
                        "join_mode_choices": JOIN_MODE_CHOICES,
                        **proc_ctx,
                        "bands": _card_bands(err_parsed, err_nodes, proc_ctx),
                    },
                    status_code=422,    # omit for the success/AI paths
                )
```

Apply the same pattern (capture `proc_ctx`, add `"bands": _card_bands(<parsed>, <node_vms>, proc_ctx)`) to
all three sites, using each site's own parsed pipeline and node-vm list.

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/plane/test_logic_bands.py tests/plane -q`
Expected: PASS (the new tests pass; the existing `tests/plane` suite stays green — `bands` is additive).

- [ ] **Step 7: Lint, type-check, commit, push**

```bash
python -m ruff check uticen_lite tests && python -m mypy uticen_lite
git add uticen_lite/plane/routes/pipeline.py tests/plane/test_logic_bands.py
git commit -m "feat: _card_bands — group node cards into Inputs + per-procedure bands"
git push -u origin HEAD
```

---

### Task 3: Builder — sectioned `<details>` UI (templates + JS + CSS)

**Files:**
- Rewrite: `uticen_lite/plane/templates/partials/_pipe_cards.html`
- Delete: `uticen_lite/plane/templates/partials/_procedures_panel.html`
- Modify: `uticen_lite/plane/templates/partials/_pipe_node.html`
- Modify: `uticen_lite/plane/templates/logic_builder.html`
- Modify: `uticen_lite/plane/static/app.css`
- Test: render assertion in `tests/plane/test_logic_bands.py` (extend) — see Step 7.

**Interfaces:**
- Consumes: `bands` context (Task 2): `bands.shared.{key,nodes}` and `bands.procedures[].{key,proc,nodes}`
  where `proc = {id, code, name, assertion, failure_threshold_pct, failure_threshold_count, color}`. Also
  the unchanged `sources`, `op_choices`, `join_mode_choices`, `control_id`, `procedures`,
  `selected_procedure_for`, `node_procedures` keys.
- Produces: DOM contract the JS depends on (exact attributes): each procedure section is
  `<details class="proc-section" data-proc-section data-band-key="<pid>">` with a `<summary>` carrying the
  header inputs `<span data-proc-head data-proc-id="<pid>">…<input data-proc-code> <input data-proc-name>
  <input data-proc-assert> <input data-proc-pct> <input data-proc-count> <button data-proc-del></span>`;
  the Inputs band is `<details class="band-inputs" data-band-key="__inputs__">`. Insert buttons carry
  `data-insert data-type data-up data-down data-proc="<pid or ''>"`. Each section body ends in its insert
  zones; a Test card keeps `<select data-procedure>`.

- [ ] **Step 1: Rewrite `_pipe_cards.html`**

Replace the entire file with (renders the Inputs band, then one `<details>` per procedure band, then the
Add-procedure button; insert zones are computed per-band off each card's own first input so splices stay
clean):

```jinja
{# Re-renderable cards fragment — the inner content of #pipe-cards.

   Cards are grouped into bands (route `_card_bands`): a shared "Inputs & shared
   steps" band on top, then one COLLAPSIBLE <details> section per procedure whose
   header IS the procedure editor (code · name · assertion · thresholds). Inserting
   a step inside a section wires it into that procedure's branch; inserting a Test
   there assigns it to that procedure (data-proc). HTMX swaps this whole fragment
   into #pipe-cards, so all handlers are delegated on the persistent #pipe-cards.

   Context: bands, sources, op_choices, join_mode_choices, control_id, procedures,
   selected_procedure_for, node_procedures. #}

{% macro insert_zone(up, down, proc, pos) %}
<div class="pipe-insert pipe-insert-{{ pos }}">
  <button type="button" class="pipe-insert-toggle" data-insert-toggle
          aria-label="Insert a step here" title="Insert a step here">+</button>
  <div class="pipe-insert-menu">
    <span class="pipe-insert-hint">Insert step</span>
    {% for t, lbl in [('import', 'Import'), ('filter', 'Filter'), ('join', 'Join'),
                      ('custom_python', 'Custom Python'), ('test', 'Test')] %}
    <button type="button" class="btn btn-sm btn-add" data-insert
            data-type="{{ t }}" data-up="{{ up }}" data-down="{{ down }}"
            data-proc="{{ proc }}">{{ lbl }}</button>
    {% endfor %}
  </div>
</div>
{% endmacro %}

{# IMPORTANT: keep `{% include "partials/_pipe_node.html" %}` at the template's
   top level inside the `for` loops (NOT inside a macro). The partial reads the
   loop's `node`, the per-loop `band_shared` flag, AND the GLOBAL `nodes` context
   var (for its input-step dropdown). A macro would not reliably expose the loop
   variable to the include — this mirrors the pre-existing working pattern. #}

{% if not bands.shared.nodes and not bands.procedures %}
<p class="muted">No steps yet — insert your first step below (start with an
  <strong>Import</strong> to bind a source).</p>
{{ insert_zone('', '', '', 'empty') }}
{% else %}

<details class="band-inputs" data-band-key="{{ bands.shared.key }}" open>
  <summary class="band-head">
    <span class="band-title">Inputs &amp; shared steps</span>
    <span class="band-sub muted">data sources and steps feeding more than one procedure</span>
  </summary>
  <div class="band-body">
    {% for node in bands.shared.nodes %}
      {{ insert_zone(node.inputs[0] if node.inputs else '', node.id, '',
                     'start' if loop.first else 'mid') }}
      {% set band_shared = true %}
      {% include "partials/_pipe_node.html" %}
    {% endfor %}
    {% if bands.shared.nodes %}{{ insert_zone((bands.shared.nodes | last).id, '', '', 'end') }}{% endif %}
  </div>
</details>

{% for band in bands.procedures %}
<details class="proc-section" data-proc-section data-band-key="{{ band.key }}" open>
  <summary class="band-head proc-head-row">
    <span class="proc-dot" style="background:{{ band.proc.color }}"></span>
    <span class="proc-head" data-proc-head data-proc-id="{{ band.proc.id }}">
      <input class="proc-in" data-proc-code   value="{{ band.proc.code }}" style="width:54px" aria-label="Code">
      <input class="proc-in" data-proc-name   value="{{ band.proc.name }}" placeholder="Name" style="flex:1;min-width:160px" aria-label="Name">
      <input class="proc-in" data-proc-assert value="{{ band.proc.assertion }}" placeholder="Assertion / category" style="flex:1;min-width:160px" aria-label="Assertion">
      <input class="proc-in" data-proc-pct    value="{{ band.proc.failure_threshold_pct if band.proc.failure_threshold_pct is not none else '' }}" placeholder="thr %" style="width:64px" aria-label="Threshold percent">
      <input class="proc-in" data-proc-count  value="{{ band.proc.failure_threshold_count if band.proc.failure_threshold_count is not none else '' }}" placeholder="count" style="width:64px" aria-label="Threshold count">
      <button type="button" class="btn btn-sm btn-ghost" data-proc-del aria-label="Remove procedure">✕</button>
    </span>
  </summary>
  <div class="band-body">
    {% if not band.nodes %}
    <p class="muted proc-empty">No test yet — insert a <strong>Test</strong> below to give this
      procedure a result.</p>
    {% endif %}
    {% for node in band.nodes %}
      {{ insert_zone(node.inputs[0] if node.inputs else '', node.id, band.proc.id,
                     'start' if loop.first else 'mid') }}
      {% set band_shared = false %}
      {% include "partials/_pipe_node.html" %}
    {% endfor %}
    {% if band.nodes %}{{ insert_zone((band.nodes | last).id, '', band.proc.id, 'end') }}
    {% else %}{{ insert_zone('', '', band.proc.id, 'empty') }}{% endif %}
  </div>
</details>
{% endfor %}

<button type="button" class="btn btn-sm btn-add" id="proc-add">＋ Add procedure</button>
{% endif %}
```

The empty-procedure-section insert zone passes `data-up=""`; `insertStep` (Step 4b) defaults the Test's
upstream to the last shared node so the new terminal is always wired (no unwired-terminal parse error).

- [ ] **Step 2: Delete the old panel partial and its include**

```bash
git rm uticen_lite/plane/templates/partials/_procedures_panel.html
```

In `logic_builder.html`, delete the block that includes it (lines ~166-169):

```jinja
{% else %}
<div class="card">
  {% include "partials/_procedures_panel.html" %}
</div>
<form method="post" ...>
```
becomes
```jinja
{% else %}
<form method="post" ...>
```

- [ ] **Step 3: `_pipe_node.html` — relabel selector, scope chips to shared cards**

In `uticen_lite/plane/templates/partials/_pipe_node.html`:

Change the Test-card "Procedure" row (lines ~131-142) to relabel and drop its redundant inline chips (the
enclosing section already names the procedure):

```jinja
    {% if procedures is defined %}
    <div class="pipe-row">
      <label>Belongs to</label>
      <select data-procedure>
        <option value="">— unassigned —</option>
        {% for p in procedures %}
        <option value="{{ p.id }}" {% if selected_procedure_for.get(node.id) == p.id %}selected{% endif %}>{{ p.code }} · {{ p.name }}</option>
        {% endfor %}
      </select>
    </div>
    {% endif %}
```

Change the support-node chips block (lines ~231-237) to render only on shared (Inputs-band) cards — a
private support card's section already says which procedure it belongs to. Guard on the `band_shared` flag
the cards macro sets:

```jinja
    {% if node.type != 'test' and band_shared|default(false) and node_procedures is defined and node_procedures.get(node.id) %}
    <div class="pipe-row pipe-chips">
      {% for chip in node_procedures.get(node.id, []) %}
      <span class="proc-chip" style="border-color:{{ chip.color }}">{{ chip.code }}</span>
      {% endfor %}
    </div>
    {% endif %}
```

- [ ] **Step 4: `logic_builder.html` JS — serialize from headers, insert-in-section, move-on-change, collapse, add/delete**

In the `<script>` block:

(a) **`serializeProcedures()`** — read section headers instead of panel rows. Replace its body:

```javascript
    function serializeProcedures() {
      var heads = document.querySelectorAll('[data-proc-head]');
      graph.procedures = Array.prototype.map.call(heads, function (head, i) {
        var pct = (head.querySelector('[data-proc-pct]') || {}).value || '';
        var cnt = (head.querySelector('[data-proc-count]') || {}).value || '';
        return {
          id: head.getAttribute('data-proc-id'),
          code: (head.querySelector('[data-proc-code]') || {}).value || ('P' + (i + 1)),
          name: (head.querySelector('[data-proc-name]') || {}).value || '',
          assertion: (head.querySelector('[data-proc-assert]') || {}).value || '',
          failure_threshold_pct: pct === '' ? null : Number(pct),
          failure_threshold_count: cnt === '' ? null : Number(cnt),
          position: i
        };
      });
    }
```

(b) **`insertStep`** — accept the owning procedure id and assign it to a new Test:

```javascript
    function insertStep(upId, downId, type, procId) {
      serialize();
      var node = newNode(type);
      if (type === 'test' && procId) { node.config.procedure_id = procId; }
      // An empty procedure section's insert zone has no upstream card (data-up="").
      // Default any non-import step's upstream to the LAST shared (Inputs-band) node
      // so a Test added into an empty section is a VALID wired terminal — an unwired
      // terminal fails parse_pipeline and the save 500s/422s (e2e-documented).
      if (!upId && type !== 'import') {
        var shared = document.querySelectorAll('[data-band-key="__inputs__"] [data-node]');
        if (shared.length) { upId = shared[shared.length - 1].getAttribute('data-node'); }
      }
      var splice = (type !== 'import');
      if (splice && upId) { node.inputs = [upId]; }
      graph.nodes.push(node);
      if (splice && upId && downId) {
        graph.nodes.forEach(function (n) {
          if (n.id !== downId) { return; }
          n.inputs = (n.inputs || []).map(function (i) { return i === upId ? node.id : i; });
        });
      }
      jsonField.value = JSON.stringify(graph);
      autosaveSubmit();
    }
```

And in the `#pipe-cards` delegated click handler (the `var ins = e.target.closest('[data-insert]')` branch),
pass the new attribute:

```javascript
      var ins = e.target.closest('[data-insert]');
      if (ins) {
        insertStep(ins.getAttribute('data-up'), ins.getAttribute('data-down'),
                   ins.getAttribute('data-type'), ins.getAttribute('data-proc'));
      }
```

(c) **Move-on-change** — changing a Test's "Belongs to" select re-groups it live. Add to `bindCards(root)`
(so it re-binds after each swap):

```javascript
      scope.querySelectorAll('[data-procedure]').forEach(function (sel) {
        if (sel._procBound) { return; }
        sel._procBound = true;
        sel.addEventListener('change', function () {
          serialize();
          jsonField.value = JSON.stringify(graph);
          autosaveSubmit();   // re-render regroups the card under its new section
        });
      });
```

(d) **`<details>` collapse persistence** — add a delegated listener + a restore-on-load pass. Add near the
end of the IIFE, and call `restoreCollapse()` after the initial `bindCards(document)` and inside the
autosave `.then` after `bindCards(cardsEl)`:

```javascript
    var COLLAPSE_PREFIX = 'cflow.logic.collapse.' + {{ control_id | tojson }} + '.';
    function restoreCollapse() {
      document.querySelectorAll('[data-band-key]').forEach(function (d) {
        var v = null;
        try { v = window.localStorage.getItem(COLLAPSE_PREFIX + d.getAttribute('data-band-key')); }
        catch (e) { v = null; }
        if (v === 'closed') { d.removeAttribute('open'); }
        else if (v === 'open') { d.setAttribute('open', ''); }
      });
    }
    document.addEventListener('toggle', function (e) {
      var d = e.target;
      if (!d.matches || !d.matches('[data-band-key]')) { return; }
      try {
        window.localStorage.setItem(COLLAPSE_PREFIX + d.getAttribute('data-band-key'),
                                    d.open ? 'open' : 'closed');
      } catch (err) { /* localStorage unavailable — collapse just won't persist */ }
    }, true);
    restoreCollapse();
```

(e) **Add / delete procedure section** — replace the old `#proc-add`/`#proc-list` block (lines ~363-393).
Add appends a new empty `<details>` section before the Add button and re-points the Test selectors; delete
removes the section. Both are delegated on `#pipe-cards` (sections live inside it):

```javascript
    function newProcedureSection(pid, code) {
      var sec = document.createElement('details');
      sec.className = 'proc-section';
      sec.setAttribute('data-proc-section', '');
      sec.setAttribute('data-band-key', pid);
      sec.setAttribute('open', '');
      sec.innerHTML =
        '<summary class="band-head proc-head-row">' +
        '<span class="proc-dot"></span>' +
        '<span class="proc-head" data-proc-head data-proc-id="' + pid + '">' +
        '<input class="proc-in" data-proc-code style="width:54px" aria-label="Code">' +
        '<input class="proc-in" data-proc-name placeholder="Name" style="flex:1;min-width:160px" aria-label="Name">' +
        '<input class="proc-in" data-proc-assert placeholder="Assertion / category" style="flex:1;min-width:160px" aria-label="Assertion">' +
        '<input class="proc-in" data-proc-pct placeholder="thr %" style="width:64px" aria-label="Threshold percent">' +
        '<input class="proc-in" data-proc-count placeholder="count" style="width:64px" aria-label="Threshold count">' +
        '<button type="button" class="btn btn-sm btn-ghost" data-proc-del aria-label="Remove procedure">✕</button>' +
        '</span></summary>' +
        '<div class="band-body"><p class="muted proc-empty">No test yet — insert a ' +
        '<strong>Test</strong> below to give this procedure a result.</p>' +
        '<div class="pipe-insert pipe-insert-empty">' +
        '<button type="button" class="pipe-insert-toggle" data-insert-toggle aria-label="Insert a step here">+</button>' +
        '<div class="pipe-insert-menu"><span class="pipe-insert-hint">Insert step</span>' +
        '<button type="button" class="btn btn-sm btn-add" data-insert data-type="test" data-up="" data-down="" data-proc="' + pid + '">Test</button>' +
        '</div></div></div>';
      sec.querySelector('[data-proc-code]').value = code;
      return sec;
    }
    var cardsRoot = document.getElementById('pipe-cards');
    cardsRoot.addEventListener('click', function (e) {
      var add = e.target.closest('#proc-add');
      if (add) {
        var pid = 'p_' + Math.random().toString(36).slice(2, 8);
        var n = cardsRoot.querySelectorAll('[data-proc-head]').length + 1;
        var code = 'P' + n;
        add.parentNode.insertBefore(newProcedureSection(pid, code), add);
        addProcedureOption(pid, code, '');
        return;
      }
      var del = e.target.closest('[data-proc-del]');
      if (del) {
        var sec = del.closest('[data-proc-section]');
        if (sec) {
          removeProcedureOption(sec.getAttribute('data-band-key'));
          sec.parentNode.removeChild(sec);
        }
        return;
      }
    });
    cardsRoot.addEventListener('input', function (e) {
      if (!e.target.matches('[data-proc-code], [data-proc-name]')) { return; }
      var head = e.target.closest('[data-proc-head]');
      if (!head) { return; }
      updateProcedureOption(head.getAttribute('data-proc-id'),
        (head.querySelector('[data-proc-code]') || {}).value || '',
        (head.querySelector('[data-proc-name]') || {}).value || '');
    });
```

Delete the now-obsolete `var procList = ...; var procAdd = ...; if (procAdd && procList) { ... }` block and
the old `newProcedureRow` function (the panel is gone). Keep `addProcedureOption`, `updateProcedureOption`,
`removeProcedureOption`, and `procOptionLabel` (still used).

- [ ] **Step 5: CSS — section/band styling; retire panel rules**

In `uticen_lite/plane/static/app.css`, replace the "Procedures panel" block (lines ~673-695, from the
comment through `#proc-add { ... }`) with section/band styles. Keep `.proc-dot`, `.proc-in` (re-key it to
`.proc-head`), `.proc-chip`, `.pipe-chips`:

```css
/* ── Procedure sections + Inputs band (Logic Builder) ─────────────────────────
   The flat card stack groups into a shared "Inputs" band + one collapsible
   <details> section per procedure whose header is the procedure editor. The
   header inputs carry `.proc-in` under `.proc-head` so they out-specify the
   global input field block (learning 0032). Accent colors are inline hex from
   procedure_color(); the chrome routes through design tokens (learning 0005). */
.band-inputs, .proc-section {
  border: 1px solid var(--border-default); border-radius: var(--radius-card);
  background: var(--bg-surface-2); margin-bottom: 14px; padding: 0 14px 8px;
}
.band-head {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  padding: 12px 0 10px; cursor: pointer; list-style: none;
}
.band-head::-webkit-details-marker { display: none; }
.band-head::before {
  content: "▸"; color: var(--text-tertiary); font-size: 12px; transition: transform .12s;
}
details[open] > .band-head::before { transform: rotate(90deg); }
.band-title { font-weight: 600; font-size: 14px; }
.band-sub { font-size: 12px; }
.band-body { padding-bottom: 6px; }
.proc-empty { font-size: 12px; margin: 8px 0; }
.proc-head { display: inline-flex; flex-wrap: wrap; gap: 8px; align-items: center; flex: 1; }
.proc-dot { width: 12px; height: 12px; border-radius: 3px; flex: 0 0 auto; background: var(--border-strong); }
.proc-head .proc-in {
  font-size: 13px; padding: 5px 8px; margin: 0; width: auto;
  background: var(--bg-input); color: var(--text-primary);
  border: 1px solid var(--border-default); border-radius: var(--radius-input);
}
.proc-head .proc-in:focus {
  outline: none; border-color: var(--accent-primary); box-shadow: 0 0 0 3px var(--accent-muted);
}
#proc-add { margin-top: 4px; }
.proc-chip {
  display: inline-flex; align-items: center; font-size: 11px; font-weight: 600;
  font-family: var(--font-mono); padding: 1px 7px; border-radius: var(--radius-badge);
  color: var(--text-secondary); background: var(--bg-surface-2);
  border: 1px solid var(--border-strong); border-left-width: 3px;
}
.pipe-chips { gap: 6px; }
```

(Clicking inside a `<summary>`'s input must not toggle the `<details>`: add a one-line guard in the JS IIFE
so typing in a header input doesn't collapse the section.)

```javascript
    document.addEventListener('click', function (e) {
      if (e.target.closest('.proc-head') && e.target.closest('summary')) { e.preventDefault(); }
    });
```

- [ ] **Step 6: Manual smoke — render the partial**

Run a quick render check to confirm the partial compiles and sections appear (no browser yet):

```bash
python -c "
from uticen_lite.pipeline.model import parse_pipeline
from uticen_lite.plane.routes.pipeline import _card_bands, _card_vm, _procedure_context
p = parse_pipeline({'nodes':[
  {'id':'src','type':'import','source_id':'s'},
  {'id':'t1','type':'test','inputs':['src'],'config':{'procedure_id':'p1','conditions':[{'column':'a','op':'not_empty'}]}},
],'procedures':[{'id':'p1','code':'P1','name':'One','position':0}]})
vms=[_card_vm(n,p,{},{},{}) for n in p.topological()]
b=_card_bands(p,vms,_procedure_context(p))
print('shared', [v['id'] for v in b['shared']['nodes']])
print('procs', [(x['key'], [v['id'] for v in x['nodes']]) for x in b['procedures']])
"
```
Expected: `shared ['src']` and `procs [('p1', ['t1'])]`.

- [ ] **Step 7: Extend the render test**

Add to `tests/plane/test_logic_bands.py` a test that the Builder GET renders `<details>` sections. Use the
plane test client fixture (look at `tests/plane/conftest.py` for the existing `client`/`app` fixture and
the helper that seeds a control with a pipeline; mirror an existing `tests/plane/test_*pipeline*` test).
Assert the response HTML contains `data-proc-section`, `data-band-key="__inputs__"`, and `data-proc-head`,
and does NOT contain `data-proc-panel` (the retired panel). If no seeding helper exists, assert via the
`_pipe_cards.html` template render through `templates.get_template(...).render(ctx)` with a hand-built
`bands` context.

- [ ] **Step 8: Run tests, lint, type-check, commit, push**

```bash
python -m pytest tests/plane -q
python -m ruff check uticen_lite tests && python -m mypy uticen_lite
git add uticen_lite/plane tests/plane/test_logic_bands.py
git commit -m "feat: sectioned Builder — collapsible per-procedure <details> sections"
git push -u origin HEAD
```

---

### Task 4: Flowchart — procedure swimlanes + server-rendered collapse

**Files:**
- Modify: `uticen_lite/plane/routes/pipeline.py` (`_diagram`, the flowchart GET route)
- Modify: `uticen_lite/plane/templates/partials/_pipe_diagram.html`
- Modify: `uticen_lite/plane/templates/logic_flowchart.html`
- Modify: `uticen_lite/plane/static/app.css` (or the `<style>` in `logic_flowchart.html`)
- Test: `tests/plane/test_logic_bands.py` (extend) — diagram view-model assertions.

**Interfaces:**
- Consumes: `group_nodes_by_band` (Task 1); existing `_diagram` internals (`_assign_lanes`, `_node_label`,
  `effective_procedures`, `procedure_color`).
- Produces: `_diagram(pipeline, counts, collapsed: frozenset[str] = frozenset()) -> dict` gains a
  `bands` key: `[{"key": pid_or_"__inputs__", "label": str, "color": str|None, "collapsed": bool,
  "row_start": int, "row_end": int}]` (row range each band's boxes occupy, inclusive). A collapsed
  procedure band contributes exactly one **summary box** (`{"summary": True, "label": "<code> · <name> —
  N steps", "count": <flagged or None>, ...}`) instead of its private boxes. The flowchart route reads
  `?collapsed=<comma-separated proc ids>` into the `frozenset` and passes it through.

- [ ] **Step 1: Write the failing diagram tests**

Add to `tests/plane/test_logic_bands.py`:

```python
from uticen_lite.plane.routes.pipeline import _diagram


def _forked():
    return parse_pipeline({
        "nodes": [
            {"id": "src", "type": "import", "source_id": "s"},
            {"id": "t1", "type": "test", "inputs": ["src"],
             "config": {"procedure_id": "p1", "conditions": [{"column": "a", "op": "not_empty"}]}},
            {"id": "t2", "type": "test", "inputs": ["src"],
             "config": {"procedure_id": "p2", "conditions": [{"column": "a", "op": "not_empty"}]}},
        ],
        "procedures": [
            {"id": "p1", "code": "P1", "name": "One", "position": 0},
            {"id": "p2", "code": "P2", "name": "Two", "position": 1},
        ],
    })


def test_diagram_exposes_procedure_bands():
    d = _diagram(_forked(), {})
    keys = [b["key"] for b in d["bands"]]
    assert "__inputs__" in keys and "p1" in keys and "p2" in keys
    for b in d["bands"]:
        assert b["row_start"] <= b["row_end"]
        assert b["collapsed"] is False


def test_diagram_collapsed_band_emits_summary_box():
    d = _diagram(_forked(), {}, collapsed=frozenset({"p2"}))
    p2 = next(b for b in d["bands"] if b["key"] == "p2")
    assert p2["collapsed"] is True
    # The collapsed band's private node (t2) is replaced by a single summary box.
    assert not any(box["id"] == "t2" for box in d["boxes"])
    assert any(box.get("summary") and box.get("band") == "p2" for box in d["boxes"])
    # The non-collapsed band's boxes still render.
    assert any(box["id"] == "t1" for box in d["boxes"])
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/plane/test_logic_bands.py -k diagram -q`
Expected: FAIL (`_diagram() got an unexpected keyword argument 'collapsed'` / `KeyError: 'bands'`).

- [ ] **Step 3: Rework `_diagram` to band-group and collapse**

Change `_diagram`'s signature to `def _diagram(pipeline: Pipeline, counts: dict[str, int], collapsed:
frozenset[str] = frozenset()) -> dict[str, Any]:` and rebuild it around `group_nodes_by_band`. The build:

1. `grouped = group_nodes_by_band(pipeline)` inside a `try/except` (best-effort → fall back to the current
   ungrouped layout with `"bands": []` on any failure, never 500).
2. Build the **render order** of node ids: `grouped["shared"]`, then for each procedure band either its
   `node_ids` (expanded) or — if its id is in `collapsed` and it has ≥1 node — a single synthetic summary
   id `"__sum__" + pid`.
3. Compute `row` (index in render order) and `lane` per the existing `_assign_lanes` logic, but over the
   **reduced** node set; for a summary node, lane = the min lane of the band's real nodes (or a fresh lane).
   Reuse `_assign_lanes` by feeding it the expanded order for non-collapsed nodes; for collapsed bands,
   assign the summary its own lane after the shared lanes.
4. **Edges:** keep edges among rendered real nodes. For an edge whose target is a collapsed private node,
   redirect the target to that band's summary id; drop edges whose *source* is a collapsed private node
   (the summary is a sink). De-duplicate.
5. **Boxes:** a real box as today, plus for each collapsed band one summary box:
   `{"id": "__sum__"+pid, "summary": True, "band": pid, "type": "procedure",
     "label": code + " · " + name + " — " + str(len(node_ids)) + " steps",
     "count": None, "row": <its row>, "lane": <its lane>, "terminal": False,
     "proc_color": color_by_pid[pid]}`.
6. **`bands`:** for each band (shared + each procedure), compute `row_start`/`row_end` = min/max row of its
   rendered boxes (a collapsed band: the single summary row), plus `label` (Inputs band: "Inputs & shared
   steps"; procedure: `code · name`), `color` (None for shared; `procedure_color(pos)` for a procedure),
   and `collapsed` (pid in `collapsed`).

Keep the existing `proc_color_by_node` colors for real boxes. Preserve the existing `procedures` legend
key. Return `{"boxes", "edges", "rows", "lanes", "procedures", "bands"}`.

Keep the function under control-flow that never raises: wrap the band logic in `try/except Exception` and on
failure return the pre-existing ungrouped view-model with `"bands": []`.

- [ ] **Step 4: Flowchart route reads `?collapsed=`**

In `logic_flowchart` (route at ~line 914), accept the query param and pass it through. Because the diagram
is computed inside `_editor_context`, add an optional `collapsed` arg there OR recompute the diagram in the
route. Simplest: in the route, after `ctx = _editor_context(...)`, recompute the diagram with the collapsed
set when present:

```python
    @app.get("/controls/{control_id}/logic/flowchart", response_class=HTMLResponse)
    def logic_flowchart(
        control_id: str,
        request: Request,
        collapsed: str = "",
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        root = request.app.state.project_root
        ctx = _editor_context(request, conn, root, control_id)
        ctx["active"] = "logic"
        ctx["logic_tab"] = "flowchart"
        collapsed_ids = frozenset(c for c in collapsed.split(",") if c)
        if collapsed_ids and ctx.get("diagram") is not None:
            control = repo.get_control(conn, control_id)
            parsed = _pipeline_for_view(control)
            if parsed is not None:
                ctx["diagram"] = _diagram(parsed, _row_counts(conn, root, parsed), collapsed_ids)
        ctx["collapsed"] = ",".join(sorted(collapsed_ids))
        # HTMX fragment request → return just the flowchart card.
        if request.headers.get("HX-Request"):
            return templates.TemplateResponse(request, "partials/_pipe_diagram_card.html", ctx)
        return templates.TemplateResponse(request, "logic_flowchart.html", ctx)
```

(Use the existing `_pipeline_for_view` and `_row_counts` helpers — confirm their names via `grep -n "def
_pipeline_for_view\|def _row_counts" uticen_lite/plane/routes/pipeline.py`.)

- [ ] **Step 5: Templates — swimlane bands + summary boxes + collapse toggles**

Create `uticen_lite/plane/templates/partials/_pipe_diagram_card.html` (the HTMX-swappable wrapper so a
collapse toggle replaces just the chart):

```jinja
<div id="flowchart-card" class="flowchart">{% include "partials/_pipe_diagram.html" %}</div>
```

In `logic_flowchart.html`, replace the `<div class="flowchart">{% include "partials/_pipe_diagram.html" %}
</div>` with `{% include "partials/_pipe_diagram_card.html" %}`.

In `_pipe_diagram.html`, before the `{% for box in diagram.boxes %}` loop, draw a faint background rect +
header label + collapse toggle per band (a band's vertical extent is `row_start..row_end`):

```jinja
  {% for band in diagram.bands %}
    {% set by1 = pad + (band.row_start * (box_h + gap_y)) - 6 %}
    {% set by2 = pad + (band.row_end * (box_h + gap_y)) + box_h + 6 %}
    <rect class="fc-band{% if band.color %} fc-band-proc{% endif %}" x="2" y="{{ by1 }}"
          width="{{ width - 4 }}" height="{{ by2 - by1 }}" rx="10"
          style="{% if band.color %}stroke:{{ band.color }}{% endif %}" />
    <a href="/controls/{{ control_id }}/logic/flowchart?collapsed={{ band.toggle_collapsed }}"
       {% if band.key != '__inputs__' %}hx-get="/controls/{{ control_id }}/logic/flowchart?collapsed={{ band.toggle_collapsed }}"
       hx-target="#flowchart-card" hx-swap="outerHTML"{% endif %}>
      <text class="fc-band-label" x="12" y="{{ by1 + 16 }}">
        {{ '▸' if band.collapsed else '▾' }} {{ band.label }}</text>
    </a>
  {% endfor %}
```

Add `toggle_collapsed` to each band view-model in `_diagram` (Step 3): the comma-joined collapsed-id set
with this band's id toggled (added if absent, removed if present); `""` for the Inputs band (not
collapsible). Render summary boxes in the existing box loop — they already carry `label`/`proc_color`;
guard the step-inspector link so a summary box (`box.summary`) is not a link:

```jinja
  {% for box in diagram.boxes %}
    {% set x = pad + (box.lane * lane_pitch) %}
    {% set y = pad + (box.row * (box_h + gap_y)) %}
    {% if box.summary %}
    <g class="fc-box fc-summary">
      <rect x="{{ x }}" y="{{ y }}" width="{{ box_w }}" height="{{ box_h }}" rx="8"
            style="{% if box.proc_color %}stroke:{{ box.proc_color }}{% endif %}" />
      <text x="{{ x + 12 }}" y="{{ y + 28 }}">{{ box.label }}</text>
    </g>
    {% else %}
    {# ... existing <a href=step-inspector> ... <g class="fc-box"> ... block unchanged ... #}
    {% endif %}
  {% endfor %}
```

- [ ] **Step 6: CSS for bands**

Add to the `<style>` in `logic_flowchart.html` (next to the `.fc-*` rules):

```css
  .fc-band { fill: var(--bg-surface-1); stroke: var(--border-default); stroke-width: 1; opacity: 0.5; }
  .fc-band-proc { opacity: 0.35; }
  .fc-band-label { fill: var(--text-secondary); font-family: var(--font-sans); font-size: 12px; font-weight: 600; cursor: pointer; }
  .fc-summary rect { fill: var(--bg-surface-3); stroke-dasharray: 4 3; }
  .fc-summary text { fill: var(--text-secondary); font-family: var(--font-sans); font-size: 12px; }
```

Ensure `logic_flowchart.html` loads htmx (check `base.html` already includes it — `grep -rn "htmx" uticen_lite/plane/templates/base.html`; the Builder already uses HTMX so it is global).

- [ ] **Step 7: Run tests, lint, type-check, commit, push**

```bash
python -m pytest tests/plane/test_logic_bands.py -q
python -m ruff check uticen_lite tests && python -m mypy uticen_lite
git add uticen_lite/plane
git commit -m "feat: Flowchart procedure swimlanes + server-rendered collapse"
git push -u origin HEAD
```

---

### Task 5: e2e browser smoke + edge cases + final sweep

**Files:**
- Modify: `tests/e2e/test_smoke.py` and/or `tests/e2e/test_multi_procedure.py`
- Verify only: whole suite, ruff, mypy, no bundle diff.

**Interfaces:**
- Consumes: the live sectioned Builder DOM (Task 3) and swimlane Flowchart (Task 4). Read both e2e files
  first to mirror their fixtures/helpers (`playwright`, the `live_server` fixture, CSV upload, control
  creation). The DOM contract to assert: `details[data-proc-section]`, `summary.band-head`,
  `[data-band-key="__inputs__"]`, insert buttons with `data-proc`, the Test `select[data-procedure]`.

- [ ] **Step 1: Install browser (once)**

Run: `python -m playwright install chromium`
Expected: chromium present (or "already installed").

- [ ] **Step 2: Run the existing e2e to see what breaks**

Run: `python -m pytest tests/e2e -m browser -q`
Expected: `test_multi_procedure.py` (and possibly `test_smoke.py`) FAIL because the panel
(`[data-proc-row]`, `#proc-add` in the old location, the flat card stack) no longer exists — the procedure
fields moved into section headers, and cards are grouped. These are **stale-test fixes** (learning 0012),
not bugs: confirm each failure is a moved-DOM expectation before editing.

- [ ] **Step 3: Update the multi-procedure smoke for the sectioned DOM**

In `tests/e2e/test_multi_procedure.py`, update selectors/assertions:
- The Builder now renders `<details data-proc-section>` per procedure. After authoring, assert there are
  two procedure sections: `page.locator('details[data-proc-section]')` has count 2.
- Procedure fields are in section headers (`[data-proc-head][data-proc-id="<pid>"]`) not panel rows — if
  the test set `[data-proc-code]`/etc., re-point those selectors into the matching section header.
- The Test card's owner select is still `[data-procedure]` (now labeled "Belongs to") — selectors unchanged.
- Keep the run/workpaper/bundle assertions unchanged (no model change). The workpaper headings and bundle
  `procedures` count are unaffected by this UI cycle.

- [ ] **Step 4: Add a collapse + insert-in-section assertion**

Add a focused assertion (in `test_smoke.py` or `test_multi_procedure.py`):
- **Collapse:** a procedure `<details>` starts `open`; click its `summary .band-head` (away from inputs)
  and assert `expect(section).not_to_have_attribute("open", ...)` / `section.evaluate("d => d.open")` is
  `False`; reload and assert it stays collapsed (localStorage). Click again to expand.
- **Insert-in-section auto-assign:** open a procedure section's insert zone, click its **Test** button
  (`details[data-proc-section] [data-insert][data-type=test][data-proc]`), then after the autosave swap
  assert the new Test card renders inside that same section and its `[data-procedure]` value equals the
  section's procedure id. (Wire the new Test's input via the input select if needed so the save validates.)

- [ ] **Step 5: Add a flowchart collapse round-trip (browser)**

Navigate to `/controls/<id>/logic/flowchart`, assert a procedure band label is present
(`text.fc-band-label`), click it, and assert the summary box appears (`g.fc-summary`) and the collapsed
band's private box is gone. (If HTMX-driven, `page.wait_for_selector('g.fc-summary')`.)

- [ ] **Step 6: Edge-case unit test — unparsable graph degrades**

Add to `tests/plane/test_logic_bands.py` a test that an unparsable/partial graph never raises and renders
in the Inputs band: build a control whose `pipeline` JSON has a node with a dangling input or no terminal,
GET `/controls/<id>/logic/builder`, assert HTTP 200 and the response contains `data-band-key="__inputs__"`
(all cards in the Inputs band, no procedure sections). Mirror the existing `tests/plane` client fixture.

- [ ] **Step 7: Full sweep — assert no bundle/contract drift**

```bash
git fetch origin main -q
git diff --name-only origin/main...HEAD | grep -E '^(contract/|uticen_lite/schema/|uticen_lite/bundle/|uticen_lite/model/workpaper.py)' && echo "BUNDLE DRIFT — STOP" || echo "no bundle drift"
python -m pytest -q
python -m ruff check . && python -m mypy uticen_lite
python -m pytest tests/e2e -m browser -q
```
Expected: `no bundle drift`; full suite green; ruff/mypy clean; e2e green.

- [ ] **Step 8: Commit, push**

```bash
git add tests
git commit -m "test: e2e + edge coverage for sectioned Builder & swimlane Flowchart"
git push -u origin HEAD
```

---

## Self-Review (run after writing — checklist for the author)

1. **Spec coverage:** grouping helper (Task 1) ✓; shared Inputs band + per-procedure sections, panel
   absorbed into headers, insert-in-section, belongs-to move, add/delete, collapse persistence (Task 3) ✓;
   flowchart swimlanes + server-render collapse (Task 4) ✓; no-bundle/contract/store change asserted (Task
   5 Step 7) ✓; legacy single-procedure (Task 1 fallback test, renders Inputs + one section) ✓; edge cases
   /never-500 (Task 5 Step 6) ✓; e2e re-run (Task 5) ✓.
2. **Type/name consistency:** `group_nodes_by_band → {"shared": [ids], "procedures": [{"id","node_ids"}]}`
   (Task 1) is consumed by `_card_bands` (Task 2) and `_diagram` (Task 4) with those exact keys. `bands`
   context shape `{shared:{key,nodes}, procedures:[{key,proc,nodes}]}` (Task 2) matches `_pipe_cards.html`
   (Task 3). DOM attrs `data-proc-section`/`data-band-key`/`data-proc-head`/`data-proc`/`data-procedure`
   are emitted in Task 3 and asserted in Task 5.
3. **No placeholders:** every code step shows the code; commands have expected output.
