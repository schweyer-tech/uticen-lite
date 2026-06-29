# Unified Logic Authoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

---

## EXECUTION RULES (read first)

- **Never ask the user for permission to continue between tasks.** Execute the full plan start to finish.
- On an unresolvable error after 2–3 attempts: note it inline in the task and skip to the next task.
- **After every `git commit`, push:**
  ```bash
  git push -u origin HEAD
  ```
- Branch is `feat/unified-logic-authoring` (already created off `main`). Stay on it.
- Gates after each task (worktree-local venv): `python -m pytest -q`, `python -m ruff check .`,
  `python -m mypy uticen_lite`. The e2e gate (`pytest tests/e2e -m browser`, after
  `playwright install chromium`) runs only in the task that touches it (Task 9).

---

**Goal:** Collapse the control's two no-code surfaces + the Python escape hatch into one **Logic** tab with **Builder / Flowchart / Python** sub-tabs; Definition becomes metadata-only.

**Architecture:** Server-rendered sub-route tabs (learning 0007). The Logic tab reuses the existing pipeline node editor (Builder), the U2 SVG (Flowchart), and the generated/escape-hatch Python (Python). Every control is viewed as a node graph via a pure `derive_builder_graph()` helper; the store schema, the pipeline compile step (graph → rule_spec/test_code, learning 0010), and `bundle.schema.json` are untouched (cardinal rule 0001).

**Tech Stack:** FastAPI + Jinja2 + HTMX; SQLite; pandas (core); the existing `uticen_lite.pipeline` model/compile.

## Global Constraints

- Python ≥3.11; ruff target `py311`, line-length 100; mypy clean on `uticen_lite`.
- Do NOT modify `contract/bundle.schema.json` or the bundle manifest shape (cardinal rule 0001).
- Store schema unchanged — no new columns, no migration, no `schema_version` bump (0010).
- All CSS colors via `var(--token)`; light/dark parity (0005).
- Logic sub-routes MUST register before the `/controls/{control_id}` catch-all (0007); register `logic` in `app.py` where `pipeline` is today (before `controls`).
- Keep the suite green and output pristine (no new warnings).

---

### Task 1: Startup banner shows both launch commands (Note #1)

**Files:**
- Modify: `uticen_lite/plane/__main__.py:8-24` (print banner before `uvicorn.run`)
- Test: `tests/plane/test_app.py` (add a unit test for a pure banner helper)

**Interfaces:**
- Produces: `uticen_lite.plane.__main__.launch_banner(host: str, port: int) -> str`

- [ ] **Step 1: Write the failing test**
```python
# tests/plane/test_app.py
from uticen_lite.plane.__main__ import launch_banner

def test_launch_banner_names_both_entry_points():
    b = launch_banner("127.0.0.1", 8765)
    assert "http://127.0.0.1:8765" in b
    assert "controlplane" in b
    assert "python -m uticen_lite.plane" in b
```

- [ ] **Step 2: Run it — expect FAIL** (`ImportError: cannot import name 'launch_banner'`)
Run: `python -m pytest tests/plane/test_app.py -k launch_banner -q`

- [ ] **Step 3: Implement**
```python
# uticen_lite/plane/__main__.py  (add helper; call it in main() before uvicorn.run)
def launch_banner(host: str, port: int) -> str:
    url = f"http://{host}:{port}"
    return (
        f"Uticen Control Plane — {url}\n"
        f"  launch with:  controlplane   (or)   python -m uticen_lite.plane"
    )
```
In `main()`, just before `uvicorn.run(...)`: `print(launch_banner(args.host, args.port))`.

- [ ] **Step 4: Run — expect PASS.** Run: `python -m pytest tests/plane/test_app.py -k launch_banner -q`

- [ ] **Step 5: Commit + push**
```bash
git add uticen_lite/plane/__main__.py tests/plane/test_app.py
git commit -m "feat(plane): startup banner names both launch commands"
git push -u origin HEAD
```

---

### Task 2: `derive_builder_graph()` — every control viewable as a node graph

A pure view-model helper: returns a pipeline graph dict for the Builder, or `None` for a raw-Python control (no graph). This is the foundation for "nodes for everything."

**Files:**
- Create: `uticen_lite/plane/logic_view.py`
- Test: `tests/plane/test_logic_view.py`

**Interfaces:**
- Produces:
  - `derive_builder_graph(control: dict, bound_source_ids: list[str]) -> dict | None`
    - `control` has keys `pipeline` (dict|None), `rule_spec` (dict|None), `test_code` (str|None), `source_ids`.
    - Returns the stored `pipeline` graph if present; else derives an `Import → Test` graph from
      `rule_spec`; else a scaffold (`Import(first bound source) → Test(no conditions)`) when there is
      no logic yet; else `None` when `test_code` is raw Python (no graph).
  - `is_raw_python(control: dict) -> bool` → `bool(control.get("test_code")) and not control.get("pipeline") and not control.get("rule_spec")`

- [ ] **Step 1: Write failing tests**
```python
# tests/plane/test_logic_view.py
from uticen_lite.plane.logic_view import derive_builder_graph, is_raw_python

def _ids(graph): return [n["id"] for n in graph["nodes"]]

def test_stored_pipeline_is_returned_verbatim():
    g = {"nodes": [{"id": "a", "type": "import", "source_id": "s"}]}
    assert derive_builder_graph({"pipeline": g}, ["s"]) is g

def test_single_source_rule_spec_becomes_import_then_test():
    rule = {"logic": "all", "severity": "high", "item_key_column": "id",
            "description_template": "x {id}",
            "conditions": [{"column": "mfa", "op": "eq", "value": False}]}
    g = derive_builder_graph({"rule_spec": rule, "source_ids": ["acc"]}, ["acc"])
    assert [n["type"] for n in g["nodes"]] == ["import", "test"]
    imp, test = g["nodes"]
    assert imp["source_id"] == "acc"
    assert test["inputs"] == [imp["id"]]
    assert test["config"]["conditions"] == rule["conditions"]
    assert test["config"]["severity"] == "high"
    assert test["config"]["item_key_column"] == "id"

def test_cross_source_rule_keeps_condition_on_test_node():
    rule = {"logic": "all", "severity": "high", "item_key_column": "pid",
            "description_template": "x", "conditions": [
                {"op": "not_exists_in", "column": "vendor_id",
                 "other_source": "vmaster", "this_key": "vendor_id", "other_key": "vendor_id"}]}
    g = derive_builder_graph({"rule_spec": rule, "source_ids": ["pay", "vmaster"]}, ["pay", "vmaster"])
    assert [n["type"] for n in g["nodes"]] == ["import", "test"]
    assert g["nodes"][0]["source_id"] == "pay"   # primary
    assert g["nodes"][1]["config"]["conditions"] == rule["conditions"]

def test_empty_control_yields_scaffold():
    g = derive_builder_graph({"source_ids": ["acc"]}, ["acc"])
    assert [n["type"] for n in g["nodes"]] == ["import", "test"]
    assert g["nodes"][0]["source_id"] == "acc"
    assert g["nodes"][1]["config"]["conditions"] == []

def test_empty_control_no_sources_scaffold_has_unbound_import():
    g = derive_builder_graph({"source_ids": []}, [])
    assert g["nodes"][0]["type"] == "import"
    assert g["nodes"][0].get("source_id") in (None, "")

def test_raw_python_returns_none():
    assert derive_builder_graph({"test_code": "def test(pop):\n    return []"}, []) is None
    assert is_raw_python({"test_code": "def test(pop): ..."}) is True
    assert is_raw_python({"rule_spec": {"conditions": []}}) is False
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: uticen_lite.plane.logic_view`)
Run: `python -m pytest tests/plane/test_logic_view.py -q`

- [ ] **Step 3: Implement**
```python
# uticen_lite/plane/logic_view.py
"""View-model helper: render any control as a node graph for the Logic ▸ Builder tab.

Pure (no DB/IO). The graph it returns is the same shape parse_pipeline()/the Builder
template consume. Derived graphs compile back to the control's existing rule_spec, so a
derive→save round-trip is bundle-identical (cardinal rule 0001, learning 0010)."""
from __future__ import annotations

from typing import Any


def is_raw_python(control: dict[str, Any]) -> bool:
    return bool(control.get("test_code")) and not control.get("pipeline") and not control.get("rule_spec")


def derive_builder_graph(control: dict[str, Any], bound_source_ids: list[str]) -> dict[str, Any] | None:
    if control.get("pipeline"):
        return control["pipeline"]
    if is_raw_python(control):
        return None
    primary = bound_source_ids[0] if bound_source_ids else None
    rule = control.get("rule_spec")
    conditions = list(rule["conditions"]) if rule else []
    test_cfg: dict[str, Any] = {
        "logic": (rule or {}).get("logic", "all"),
        "severity": (rule or {}).get("severity", "medium"),
        "item_key_column": (rule or {}).get("item_key_column"),
        "description_template": (rule or {}).get("description_template", ""),
        "conditions": conditions,
    }
    return {
        "nodes": [
            {"id": "src", "type": "import", "source_id": primary, "narrative": ""},
            {"id": "tst", "type": "test", "inputs": ["src"], "narrative": "", "config": test_cfg},
        ]
    }
```

- [ ] **Step 4: Run — expect PASS.** Run: `python -m pytest tests/plane/test_logic_view.py -q`

- [ ] **Step 5: Commit + push**
```bash
git add uticen_lite/plane/logic_view.py tests/plane/test_logic_view.py
git commit -m "feat(plane): derive_builder_graph — view any control as a node graph"
git push -u origin HEAD
```

---

### Task 3: Logic sub-routes + tab nav (shell, reusing current pipeline content)

Rename the pipeline route module to `logic.py` and expose the three sub-routes; add the sub-tab nav include; flip the top-level tab. Builder keeps rendering the existing pipeline editor for now (Task 4 swaps in derivation); Flowchart and Python render the existing diagram/python sections extracted into their own templates is Task 4 — here just get routing + nav green.

**Files:**
- Rename/modify: `uticen_lite/plane/routes/pipeline.py` → keep filename but rename `register` content; add routes `GET /controls/{id}/logic` (302→builder), `/logic/builder`, `/logic/flowchart`, `/logic/python`, `POST /controls/{id}/logic/builder` (was `/pipeline`), `POST /controls/{id}/logic/convert`. Keep `GET /controls/{id}/pipeline` as a 301 redirect to `/logic/builder`.
- Create: `uticen_lite/plane/templates/partials/_logic_tabs.html`
- Modify: `uticen_lite/plane/templates/partials/_control_tabs.html:1-5` (Pipeline → Logic, href `/controls/{{ control.id }}/logic/builder`, active key `logic`)
- Modify: `uticen_lite/plane/app.py:75` (registration comment/name stays `pipeline.register` — module unchanged path)
- Test: `tests/plane/test_pipeline_editor.py` (add sub-route + redirect + nav tests)

**Interfaces:**
- Consumes: `_editor_context(...)` (unchanged, from current pipeline.py).
- Produces: routes above; `_logic_tabs.html` expects `control` + `logic_tab` in ('builder','flowchart','python').

- [ ] **Step 1: Write failing tests**
```python
# tests/plane/test_pipeline_editor.py  (add)
def test_logic_subroutes_render(client, seeded_pipeline_control):  # reuse existing fixture
    cid = seeded_pipeline_control
    for sub in ("builder", "flowchart", "python"):
        r = client.get(f"/controls/{cid}/logic/{sub}")
        assert r.status_code == 200
        assert 'class="tab active"' in r.text  # a sub-tab is active

def test_logic_bare_redirects_to_builder(client, seeded_pipeline_control):
    r = client.get(f"/controls/{seeded_pipeline_control}/logic", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"].endswith("/logic/builder")

def test_old_pipeline_url_redirects(client, seeded_pipeline_control):
    r = client.get(f"/controls/{seeded_pipeline_control}/pipeline", follow_redirects=False)
    assert r.status_code in (301, 308)
    assert r.headers["location"].endswith("/logic/builder")

def test_control_tab_says_logic_not_pipeline(client, seeded_pipeline_control):
    r = client.get(f"/controls/{seeded_pipeline_control}/logic/builder")
    assert ">Logic<" in r.text and ">Pipeline<" not in r.text
```
(If `seeded_pipeline_control` doesn't exist, add a fixture that POSTs a control then saves a 2-node pipeline via the existing save path; mirror the setup already in `test_pipeline_editor.py`.)

- [ ] **Step 2: Run — expect FAIL** (404s / "Pipeline" still present)
Run: `python -m pytest tests/plane/test_pipeline_editor.py -k "logic or redirect or tab" -q`

- [ ] **Step 3: Implement**
- In `pipeline.py register()`: add `@app.get("/controls/{control_id}/logic")` → `RedirectResponse(f"/controls/{control_id}/logic/builder", status_code=302)`. Add `/logic/builder`, `/logic/flowchart`, `/logic/python` GETs that call `_editor_context(...)` and render the (Task-4) per-tab templates with `logic_tab` set; for now point all three at `control_pipeline.html` and pass `logic_tab`. Change `POST /controls/{id}/pipeline` path to `/controls/{id}/logic/builder` (keep handler body). Change convert to `POST /controls/{id}/logic/convert` and redirect target to `/controls/{control_id}/logic/python`. Add `@app.get("/controls/{control_id}/pipeline")` → `RedirectResponse(".../logic/builder", status_code=301)`.
- `_control_tabs.html`:
```html
<a href="/controls/{{ control.id }}/logic/builder" class="tab {% if active == 'logic' %}active{% endif %}">Logic</a>
```
- `_logic_tabs.html`:
```html
<nav class="subtabs">
  <a href="/controls/{{ control.id }}/logic/builder" class="tab {% if logic_tab == 'builder' %}active{% endif %}">Builder</a>
  <a href="/controls/{{ control.id }}/logic/flowchart" class="tab {% if logic_tab == 'flowchart' %}active{% endif %}">Flowchart</a>
  <a href="/controls/{{ control.id }}/logic/python" class="tab {% if logic_tab == 'python' %}active{% endif %}">Python</a>
</nav>
```
- In `control_pipeline.html`, after the `_control_tabs.html` include (line ~72) add `{% include "partials/_logic_tabs.html" %}` and set `active='logic'` in the route context.

- [ ] **Step 4: Run — expect PASS.** Run: `python -m pytest tests/plane/test_pipeline_editor.py -q`

- [ ] **Step 5: Commit + push**
```bash
git add uticen_lite/plane/routes/pipeline.py uticen_lite/plane/templates/partials/_logic_tabs.html uticen_lite/plane/templates/partials/_control_tabs.html uticen_lite/plane/templates/control_pipeline.html tests/plane/test_pipeline_editor.py
git commit -m "feat(plane): Logic tab with builder/flowchart/python sub-routes (+ /pipeline redirect)"
git push -u origin HEAD
```

---

### Task 4: Split the page — Builder / Flowchart / Python render separately; Builder uses derivation

Break `control_pipeline.html` into three templates so each sub-route shows only its pane, and make the Builder render `derive_builder_graph(...)` for non-pipeline controls.

**Files:**
- Create: `uticen_lite/plane/templates/logic_builder.html` (Steps editor + toolbar + Save; the JS editor block), `logic_flowchart.html` (the `{% include "partials/_pipe_diagram.html" %}` + heading), `logic_python.html` (Task 5).
- Modify: `uticen_lite/plane/routes/pipeline.py` — the three GETs render their own template; `_editor_context` gains a `builder_graph` (call `derive_builder_graph`) and a `raw_python` flag, so Builder shows the node editor for a derived graph or the "authored in Python" notice when `derive_builder_graph` returns `None`.
- Delete: `control_pipeline.html` once the three templates cover it (or keep as a thin base they extend).
- Test: `tests/plane/test_pipeline_editor.py`

- [ ] **Step 1: Write failing tests**
```python
def test_builder_shows_nodes_for_rule_control(client):
    cid = _make_rule_control(client)   # a simple no-code rule via the save path
    r = client.get(f"/controls/{cid}/logic/builder")
    assert "Import" in r.text and "Test" in r.text          # derived 2-node graph
    assert "Generated Python" not in r.text                 # python moved to its own tab

def test_flowchart_tab_has_svg_only(client, seeded_pipeline_control):
    r = client.get(f"/controls/{seeded_pipeline_control}/logic/flowchart")
    assert "<svg" in r.text
    assert "+ Import" not in r.text                         # no builder toolbar here

def test_builder_shows_python_notice_for_raw_python(client):
    cid = _make_raw_python_control(client)                  # test_kind=python, no graph
    r = client.get(f"/controls/{cid}/logic/builder")
    assert "authored directly in Python" in r.text
```

- [ ] **Step 2: Run — expect FAIL.** Run: `python -m pytest tests/plane/test_pipeline_editor.py -k "builder or flowchart" -q`

- [ ] **Step 3: Implement** — move the Steps card + editor JS into `logic_builder.html`; the diagram into `logic_flowchart.html`. In `_editor_context`, compute `builder_graph = derive_builder_graph(control, control["source_ids"])` and use it (instead of only the stored graph) to render nodes; when `builder_graph is None`, render the notice partial. The three GET handlers render `logic_builder.html` / `logic_flowchart.html` / `logic_python.html` respectively, each including `_control_tabs.html` (active='logic') + `_logic_tabs.html` (logic_tab=…).

- [ ] **Step 4: Run — expect PASS.** Run: `python -m pytest tests/plane/test_pipeline_editor.py -q`

- [ ] **Step 5: Commit + push**
```bash
git add uticen_lite/plane/templates/logic_builder.html uticen_lite/plane/templates/logic_flowchart.html uticen_lite/plane/routes/pipeline.py tests/plane/test_pipeline_editor.py
git commit -m "feat(plane): split Logic into Builder/Flowchart panes; Builder derives a graph for every control"
git push -u origin HEAD
```

---

### Task 5: Python sub-tab — generated (read) + escape hatch (edit) + convert

Relocate the CodeMirror raw-Python editor here. Graph controls show read-only generated Python + "Convert to Python test →"; raw-Python controls get the editable editor saving via a new POST.

**Files:**
- Create: `uticen_lite/plane/templates/logic_python.html` (move CodeMirror CSS/JS includes + textarea from `control_edit.html:151-176`).
- Modify: `uticen_lite/plane/routes/pipeline.py` — `GET /logic/python` passes `generated_python`, `raw_python` flag, and `test_code`; add `POST /controls/{id}/logic/python` saving raw `test_code` (test_kind="python", pipeline=None) reusing the existing convert/upsert pattern (pipeline.py:498-511). Convert button already targets `/logic/convert` → redirects to `/logic/python` (Task 3).
- Test: `tests/plane/test_pipeline_editor.py`

- [ ] **Step 1: Write failing tests**
```python
def test_python_tab_readonly_generated_for_graph_control(client, seeded_pipeline_control):
    r = client.get(f"/controls/{seeded_pipeline_control}/logic/python")
    assert "def test(" in r.text
    assert "Convert to Python test" in r.text

def test_python_tab_editable_for_raw_python(client):
    cid = _make_raw_python_control(client)
    r = client.get(f"/controls/{cid}/logic/python")
    assert 'name="test_code"' in r.text                      # editable textarea present
    # save edits
    r2 = client.post(f"/controls/{cid}/logic/python",
                     data={"test_code": "def test(pop):\n    return []"}, follow_redirects=False)
    assert r2.status_code in (303, 302)
```

- [ ] **Step 2: Run — expect FAIL.** Run: `python -m pytest tests/plane/test_pipeline_editor.py -k python -q`
- [ ] **Step 3: Implement** per Files above.
- [ ] **Step 4: Run — expect PASS.** Run: `python -m pytest tests/plane/test_pipeline_editor.py -q`
- [ ] **Step 5: Commit + push**
```bash
git add uticen_lite/plane/templates/logic_python.html uticen_lite/plane/routes/pipeline.py tests/plane/test_pipeline_editor.py
git commit -m "feat(plane): Logic ▸ Python tab — generated view + relocated escape hatch"
git push -u origin HEAD
```

---

### Task 6: Definition tab → metadata only

Remove the Test-logic section from the Definition template and make the Definition POST save metadata + sources only (no rule/python parsing). New controls are created with no logic; the Builder derives a scaffold on first view (Task 2/4).

**Files:**
- Modify: `uticen_lite/plane/templates/control_edit.html` — delete lines 108–143 (Test logic section) and the CodeMirror + condition-row JS at 151–211 (moved to logic_python.html / logic_builder.html). Keep Details, Failure thresholds, Data sources, Submit.
- Modify: `uticen_lite/plane/routes/controls.py` — `_save_from_form` (253–309): drop the `test_kind` rule/python/pipeline branching; save only metadata + framework + thresholds + sources; pass `test_kind`/`rule_spec`/`test_code`/`pipeline` through **unchanged** for an existing control (load current values and re-upsert them) so editing metadata never clobbers logic. For a NEW control, create with `test_kind="pipeline"`, `pipeline=None`, `rule_spec=None`, `test_code=None` (Builder seeds on view). The `_rule_spec_from_form`, `_conditions_view_from_form`, `_condition_row`/`_conditions` partial endpoints **move with the rule builder** (they are used by the Builder's Test-node condition editor) — keep them registered.
- Test: `tests/plane/test_controls.py`

- [ ] **Step 1: Write failing tests**
```python
def test_definition_has_no_test_logic(client, a_control):
    r = client.get(f"/controls/{a_control}")
    assert "Test logic" not in r.text
    assert 'name="test_code"' not in r.text
    assert 'name="test_kind"' not in r.text

def test_editing_metadata_preserves_existing_logic(client):
    cid = _make_rule_control(client)                         # has a rule_spec
    before = _get_control(cid)                               # helper: read store
    client.post(f"/controls/{cid}", data={"id": cid, "title": "New title",
                "objective": "o", "narrative": "n", "framework_nist": "",
                "failure_threshold_count": "0", "source_ids": before["source_ids"]})
    after = _get_control(cid)
    assert after["title"] == "New title"
    assert after["rule_spec"] == before["rule_spec"]         # logic untouched
```

- [ ] **Step 2: Run — expect FAIL.** Run: `python -m pytest tests/plane/test_controls.py -k "test_logic or preserves" -q`
- [ ] **Step 3: Implement** per Files above.
- [ ] **Step 4: Run — expect PASS.** Run: `python -m pytest tests/plane/test_controls.py -q`
- [ ] **Step 5: Commit + push**
```bash
git add uticen_lite/plane/templates/control_edit.html uticen_lite/plane/routes/controls.py tests/plane/test_controls.py
git commit -m "feat(plane): Definition tab is metadata-only; logic authoring moves to the Logic tab"
git push -u origin HEAD
```

---

### Task 7: Cross-source rule round-trips through the Builder

Guarantee a derived **cross-source** rule (`not_exists_in`) survives a Builder save unchanged: derive → render on the Test node → save → compile back to the identical `rule_spec`.

**Files:**
- Modify (only if needed): `uticen_lite/plane/templates/partials/_pipe_node.html` — ensure a Test node's condition rows offer the cross-source ops + the `other_source`/`this_key`/`other_key` fields (reuse the same widgets the rule builder uses in `rule_conditions.html`).
- Verify: `uticen_lite/pipeline/compile.py:_try_pure_rule_spec` passes a cross-source condition through to the emitted `rule_spec` for a single `Import → Test` (no Join). If it currently rejects/strips it, extend it minimally to pass cross-source condition dicts through unchanged.
- Test: `tests/plane/test_logic_view.py` + `tests/pipeline/test_compile.py` (or wherever compile is tested)

- [ ] **Step 1: Write the failing round-trip test**
```python
# tests/plane/test_logic_view.py
from uticen_lite.pipeline.model import parse_pipeline
from uticen_lite.pipeline.compile import compile_pipeline
from uticen_lite.plane.logic_view import derive_builder_graph

def test_cross_source_rule_round_trips_to_same_rule_spec():
    rule = {"logic": "all", "severity": "high", "item_key_column": "pid",
            "description_template": "x", "conditions": [
                {"op": "not_exists_in", "column": "vendor_id",
                 "other_source": "vmaster", "this_key": "vendor_id", "other_key": "vendor_id"}]}
    g = derive_builder_graph({"rule_spec": rule, "source_ids": ["pay", "vmaster"]}, ["pay", "vmaster"])
    compiled = compile_pipeline(parse_pipeline(g))
    assert compiled.rule_spec is not None
    assert compiled.rule_spec["conditions"] == rule["conditions"]
```

- [ ] **Step 2: Run — expect FAIL or PASS.** Run: `python -m pytest tests/plane/test_logic_view.py -k cross_source_rule_round -q`
  - If it PASSES already, note that in the task and skip Steps 3 (no compile change needed); still do the template check.
- [ ] **Step 3: Implement** the minimal compile/template change to make it pass (only if it failed).
- [ ] **Step 4: Run — expect PASS.** Re-run the test above + `python -m pytest tests/pipeline -q`.
- [ ] **Step 5: Commit + push**
```bash
git add uticen_lite/pipeline/compile.py uticen_lite/plane/templates/partials/_pipe_node.html tests/plane/test_logic_view.py tests/pipeline/test_compile.py
git commit -m "feat(pipeline): cross-source rule round-trips through a single Import→Test (Builder)"
git push -u origin HEAD
```

---

### Task 8: Reconcile the existing plane unit tests with the new structure

Update tests that asserted the old Definition test-logic / Pipeline tab so the suite is green and meaningful.

**Files:**
- Modify: `tests/plane/test_rule_builder.py` (the `_rule_spec_from_form`/`_conditions_view_from_form` helper tests stay — those helpers still serve the Builder's Test-node condition editor; update any that fetched the rule builder from the Definition page to fetch it from `/logic/builder`).
- Modify: `tests/plane/test_controls.py` (Python-authoring assertions move to the Logic ▸ Python tab; metadata-only Definition).
- Modify: `tests/plane/test_pipeline_editor.py` (route paths `pipeline` → `logic/builder`; the "not shadowed" test now targets `/logic/builder`).

- [ ] **Step 1: Run the full plane suite, list failures**
Run: `python -m pytest tests/plane -q`
- [ ] **Step 2: Fix each failing assertion** to match the new routes/structure (no behavior change to the product — only test expectations + URLs). Keep each fix minimal; do not weaken a test to pass.
- [ ] **Step 3: Run — expect PASS.** Run: `python -m pytest tests/plane -q`
- [ ] **Step 4: Full gates.** Run: `python -m pytest -q && python -m ruff check . && python -m mypy uticen_lite`
- [ ] **Step 5: Commit + push**
```bash
git add tests/plane
git commit -m "test(plane): reconcile control-editor tests with the Logic-tab restructure"
git push -u origin HEAD
```

---

### Task 9: Rewrite the e2e browser smoke (learning 0012)

The smoke authors a rule in Definition; move that to Logic ▸ Builder. This is the load-bearing browser gate.

**Files:**
- Modify: `tests/e2e/test_smoke.py:59-96` (step 3). New flow: create the control (Definition: id/title/threshold + check the `users` source) → go to `/controls/{id}/logic/builder` → on the seeded `Import → Test` scaffold, set the Import source to `users`, fill the Test node's conditions (`can_create not_empty`, etc., engineered so exactly U1 is flagged), Save → assert run/export downstream unchanged.

- [ ] **Step 1: Rewrite step 3** against the real rendered HTML (open the Builder, read the actual node-card field `name`s with the TestClient first, then write Playwright selectors to match — per learning 0012, target by the assembled DOM).
- [ ] **Step 2: Install the browser** (if not present): `python -m playwright install chromium`
- [ ] **Step 3: Run the smoke — expect PASS.** Run: `python -m pytest tests/e2e -m browser -q`
  - The export/contract assertions (steps 5–6) must still pass unchanged (cardinal rule).
- [ ] **Step 4: Commit + push**
```bash
git add tests/e2e/test_smoke.py
git commit -m "test(e2e): author via Logic ▸ Builder instead of Definition (0012)"
git push -u origin HEAD
```

---

### Task 10: Refresh PRODUCT-MAP

**Files:**
- Modify: `PRODUCT-MAP.md` — the "Control editor" row (no longer hosts test logic; metadata + sources only), and rename/rewrite the "Pipeline editor" row to "Control plane — Logic (Builder / Flowchart / Python)". Bump "Last updated".

- [ ] **Step 1: Edit the two rows** to describe: Definition = metadata + sources; Logic = one area with Builder (node graph; every control viewable as nodes) / Flowchart / Python (generated + escape hatch); store/compile/bundle unchanged.
- [ ] **Step 2: Commit + push**
```bash
git add PRODUCT-MAP.md
git commit -m "docs(product-map): Definition is metadata-only; Logic = Builder/Flowchart/Python"
git push -u origin HEAD
```

---

## Final gate (after Task 10)

- [ ] `python -m pytest -q` → green (with `[adapters]` installed, else ignore the openpyxl-only failures).
- [ ] `python -m ruff check .` → clean · `python -m mypy uticen_lite` → clean.
- [ ] `python -m pytest tests/e2e -m browser -q` → 1 passed.
- [ ] `python -m pytest tests/test_contract_export.py tests/schema/test_bundle_schema.py -q` → green (cardinal rule intact).
- [ ] Open a PR `feat/unified-logic-authoring` → `main`; do not self-merge without the user's go-ahead.
