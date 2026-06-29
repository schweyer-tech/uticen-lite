# Procedure header owns procedure identity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Logic Builder's procedure section header the complete procedure editor (add narrative) and reduce the Test node card to pure step mechanics, while preserving the single-procedure heading byte-identity invariant.

**Architecture:** Pure consolidation of the existing sectioned Builder. Procedure metadata already round-trips through the section headers (`serializeProcedures` ↔ `graph.procedures` ↔ `ProcedureDef`); this thread `narrative` through the one missing view-model + template + serialize site, deletes two vestigial node fields, and fixes a sole-procedure code default. One demo control gains procedure narratives. No bundle/schema change.

**Tech Stack:** Python ≥3.11 (FastAPI + HTMX + Jinja2 + `sqlite3`, `[plane]` extra), vanilla JS in `logic_builder.html`, Playwright e2e, pytest, ruff (py311, line 100), mypy.

## EXECUTION RULES

- Never ask the user for permission to continue between tasks. Execute the full plan start to finish without interruption.
- After every `git commit`, push:
  ```bash
  git push -u origin HEAD
  ```
- On an unresolvable error after 2–3 attempts: note it in the progress ledger and skip to the next task.

## Global Constraints

(Every task's requirements implicitly include this section. Values copied from the spec.)

- **Cardinal rule:** no change to `contract/bundle.schema.json` or the bundle `schema_version`. Procedure code/name/assertion/threshold/verdict stay **render+store-only** (learnings 0001, 0015). The contract gate (`tests/test_contract_export.py` + `tests/schema/test_bundle_schema.py`) must stay green.
- **Sole-procedure heading byte-identity (learning 0036):** a control with exactly ONE effective procedure must keep `code=""` so the workpaper heading stays the legacy `P1: title` form (a non-empty code emits `P1 &middot; title`). Author-typed codes are always preserved verbatim.
- **Thread new context keys through every render site (learning 0038):** `narrative` is added to the `band.proc` view-model in the single context builder `_procedure_context` and its `_card_bands` fallback default — not per-render-site.
- **Pyodide-safe core:** no pandas in `uticen_lite/pipeline/`.
- **e2e on form/HTMX restructure (learning 0012):** re-run + update `pytest tests/e2e -m browser` when the Test form or procedure header changes shape. Assert app-persisted state via the app's own write path; never inject state to dodge a race (learning 0037).
- Gates: `python -m pytest -q` pristine (no new warnings), `python -m ruff check .`, `python -m mypy uticen_lite`. ruff target `py311`, line length 100.

---

### Task 1: Thread procedure narrative into the view-model + header editor

**Files:**
- Modify: `uticen_lite/plane/routes/pipeline.py` (`_procedure_context` ~698-709; `_card_bands` `_proc_defaults` ~753-756)
- Modify: `uticen_lite/plane/templates/partials/_pipe_cards.html` (the `proc-head` span ~61-68)
- Modify: `uticen_lite/plane/templates/logic_builder.html` (`serializeProcedures()` ~303-318; `newProcedureSection()` innerHTML ~353-370)
- Modify: `uticen_lite/plane/static/app.css` (after `.proc-head .proc-in:focus` ~705)
- Test: `tests/plane/test_logic_bands.py`

**Interfaces:**
- Consumes: `parse_pipeline` already parses `procedures[].narrative` into `ProcedureDef.narrative` (model.py:200) — no parser change needed.
- Produces: `_procedure_context(pipeline)["procedures"][i]["narrative"]` (str); `band.proc.narrative` reachable in `_pipe_cards.html`; `graph.procedures[i].narrative` written by `serializeProcedures()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/plane/test_logic_bands.py`:

```python
def test_procedure_context_includes_narrative():
    pipe = parse_pipeline({
        "nodes": [
            {"id": "src", "type": "import", "source_id": "s"},
            {"id": "t1", "type": "test", "inputs": ["src"],
             "config": {"procedure_id": "p1", "conditions": [{"column": "a", "op": "not_empty"}]}},
        ],
        "procedures": [
            {"id": "p1", "code": "P1", "name": "One",
             "narrative": "Why we test this", "position": 0},
        ],
    })
    ctx = _procedure_context(pipe)
    assert ctx["procedures"][0]["narrative"] == "Why we test this"


def test_card_bands_proc_carries_narrative():
    pipe = parse_pipeline({
        "nodes": [
            {"id": "src", "type": "import", "source_id": "s"},
            {"id": "t1", "type": "test", "inputs": ["src"],
             "config": {"procedure_id": "p1", "conditions": [{"column": "a", "op": "not_empty"}]}},
        ],
        "procedures": [
            {"id": "p1", "code": "P1", "name": "One",
             "narrative": "Why we test this", "position": 0},
        ],
    })
    bands = _card_bands(pipe, _vms(pipe), _procedure_context(pipe))
    assert bands["procedures"][0]["proc"]["narrative"] == "Why we test this"


def test_builder_get_renders_procedure_narrative_field(client):
    """The procedure section header exposes an editable narrative field, pre-filled
    from the procedure's narrative."""
    csv = b"user_id,can_create\nU1,true\nU2,\n"
    client.post("/sources", data={"source_id": "users", "format": "csv"},
                files={"file": ("users.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)
    client.post("/controls", data={"id": "c1", "title": "C1", "objective": "o",
                "narrative": "n", "source_ids": ["users"], "failure_threshold_count": "0"},
                follow_redirects=False)
    graph = {
        "nodes": [
            {"id": "src", "type": "import", "source_id": "users"},
            {"id": "tst", "type": "test", "inputs": ["src"],
             "config": {"logic": "all", "procedure_id": "p1",
                        "conditions": [{"column": "can_create", "op": "not_empty"}]}},
        ],
        "procedures": [{"id": "p1", "code": "P1", "name": "One",
                        "narrative": "Reviewer independence", "position": 0}],
    }
    client.post("/controls/c1/logic/builder",
                data={"pipeline_json": json.dumps(graph)}, follow_redirects=False)
    page = client.get("/controls/c1/logic/builder").text
    assert "data-proc-narrative" in page
    assert "Reviewer independence" in page
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/plane/test_logic_bands.py -q`
Expected: FAIL — `KeyError: 'narrative'` (context test) and `assert 'data-proc-narrative' in page` is False.

- [ ] **Step 3: Add `narrative` to the view-model (`routes/pipeline.py`)**

In `_procedure_context`, add the `narrative` key to each procedure dict (the dict at ~698-709):

```python
        procedures = [
            {
                "id": p.id,
                "code": p.code or f"P{i + 1}",
                "name": p.name,
                "assertion": p.assertion,
                "narrative": p.narrative,
                "failure_threshold_pct": p.failure_threshold_pct,
                "failure_threshold_count": p.failure_threshold_count,
                "color": color_by_pid[p.id],
            }
            for i, p in enumerate(eff)
        ]
```

In `_card_bands`, add `narrative` to `_proc_defaults` (~753-756) so an orphan band (proc not in `proc_by_id`) still has the key:

```python
        _proc_defaults: dict[str, Any] = {
            "code": "", "name": "", "assertion": "", "narrative": "",
            "failure_threshold_pct": None, "failure_threshold_count": None, "color": "#888",
        }
```

- [ ] **Step 4: Add the narrative field to the header template (`_pipe_cards.html`)**

In the `proc-head` `<span>`, add a full-width narrative textarea as the LAST child (after the `data-proc-del` button, ~line 67), so it wraps onto its own row:

```html
      <button type="button" class="btn btn-sm btn-ghost" data-proc-del aria-label="Remove procedure">✕</button>
      <textarea class="proc-in proc-narr" data-proc-narrative rows="2"
                placeholder="Procedure narrative — what this procedure tests and why"
                aria-label="Procedure narrative">{{ band.proc.narrative }}</textarea>
```

- [ ] **Step 5: Mirror the field in `newProcedureSection()` (`logic_builder.html`)**

In the `newProcedureSection` innerHTML, add the narrative textarea after the `data-proc-del` button line (~362), keeping it inside the `.proc-head` span (before `'</span></summary>'`):

```javascript
        '<button type="button" class="btn btn-sm btn-ghost" data-proc-del aria-label="Remove procedure">✕</button>' +
        '<textarea class="proc-in proc-narr" data-proc-narrative rows="2" ' +
        'placeholder="Procedure narrative — what this procedure tests and why" ' +
        'aria-label="Procedure narrative"></textarea>' +
        '</span></summary>' +
```

- [ ] **Step 6: Read the narrative in `serializeProcedures()` (`logic_builder.html`)**

In `serializeProcedures()` (~305-317), add the narrative to the returned object:

```javascript
      graph.procedures = Array.prototype.map.call(heads, function (head, i) {
        var pct = (head.querySelector('[data-proc-pct]') || {}).value || '';
        var cnt = (head.querySelector('[data-proc-count]') || {}).value || '';
        return {
          id: head.getAttribute('data-proc-id'),
          code: (head.querySelector('[data-proc-code]') || {}).value || ('P' + (i + 1)),
          name: (head.querySelector('[data-proc-name]') || {}).value || '',
          assertion: (head.querySelector('[data-proc-assert]') || {}).value || '',
          narrative: (head.querySelector('[data-proc-narrative]') || {}).value || '',
          failure_threshold_pct: pct === '' ? null : Number(pct),
          failure_threshold_count: cnt === '' ? null : Number(cnt),
          position: i
        };
      });
```

(The `code` default is fixed in Task 3 — leave it as `('P' + (i + 1))` here for now.)

- [ ] **Step 7: Style the narrative row (`app.css`)**

After the `.proc-head .proc-in:focus` rule (~705), add:

```css
.proc-head .proc-narr {
  flex: 1 0 100%; width: 100%; resize: vertical; min-height: 34px;
  font-family: inherit; line-height: 1.4;
}
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python -m pytest tests/plane/test_logic_bands.py -q`
Expected: PASS (all three new tests + the existing band tests).

- [ ] **Step 9: Gates + commit + push**

```bash
python -m ruff check . && python -m mypy uticen_lite
git add uticen_lite/plane/routes/pipeline.py uticen_lite/plane/templates/partials/_pipe_cards.html uticen_lite/plane/templates/logic_builder.html uticen_lite/plane/static/app.css tests/plane/test_logic_bands.py
git commit -m "feat(plane): add procedure narrative to the section-header editor"
git push -u origin HEAD
```

---

### Task 2: Reduce the Test node card to pure step mechanics

**Files:**
- Modify: `uticen_lite/plane/templates/partials/_pipe_node.html` (the `{% if node.type == 'test' %}` block, rows at ~121-126 and ~141-150)
- Modify: `uticen_lite/plane/templates/logic_builder.html` (`serialize()` Test branch, ~244-255)
- Test: `tests/plane/test_logic_bands.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: a Test node's serialized `config` carries only `procedure_id`, `severity`, `description_template`, `item_key_column`, `logic`, `conditions` — never `title` or `failure_threshold_pct/count`.

- [ ] **Step 1: Write the failing test**

Add to `tests/plane/test_logic_bands.py`:

```python
def test_test_node_card_has_no_procedure_identity_fields(client):
    """The Test node card carries step mechanics only — the 'Procedure title' and
    per-node Threshold fields moved to the procedure header. The 'Belongs to'
    selector and Severity stay."""
    csv = b"user_id,can_create\nU1,true\nU2,\n"
    client.post("/sources", data={"source_id": "users", "format": "csv"},
                files={"file": ("users.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)
    client.post("/controls", data={"id": "c1", "title": "C1", "objective": "o",
                "narrative": "n", "source_ids": ["users"], "failure_threshold_count": "0"},
                follow_redirects=False)
    graph = {
        "nodes": [
            {"id": "src", "type": "import", "source_id": "users"},
            {"id": "tst", "type": "test", "inputs": ["src"],
             "config": {"logic": "all", "procedure_id": "p1",
                        "conditions": [{"column": "can_create", "op": "not_empty"}]}},
        ],
        "procedures": [{"id": "p1", "code": "P1", "name": "One", "position": 0}],
    }
    client.post("/controls/c1/logic/builder",
                data={"pipeline_json": json.dumps(graph)}, follow_redirects=False)
    page = client.get("/controls/c1/logic/builder").text
    # Vestigial procedure fields are gone from every Test node card and the serializer.
    assert "data-proc-title" not in page
    assert "data-threshold-pct" not in page
    assert "data-threshold-count" not in page
    # Genuine step mechanics remain.
    assert "data-procedure" in page   # "Belongs to" selector
    assert "data-severity" in page
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/plane/test_logic_bands.py::test_test_node_card_has_no_procedure_identity_fields -q`
Expected: FAIL — `assert "data-proc-title" not in page` is False (the field is rendered, and `serialize()` references it).

- [ ] **Step 3: Remove the two vestigial rows from `_pipe_node.html`**

In the `{% if node.type == 'test' %}` block, DELETE the "Procedure title" row (currently ~121-126):

```html
    <div class="pipe-row">
      <label>Procedure title</label>
      <input type="text" data-proc-title style="flex:1;min-width:240px;"
             placeholder="Procedure title (optional)"
             value="{{ node.config.get('title', '') }}">
    </div>
```

and DELETE the "Threshold %/Count" row (currently ~141-150):

```html
    <div class="pipe-row">
      <label>Threshold %</label>
      <input type="text" data-threshold-pct style="width:80px;"
             placeholder="e.g. 5"
             value="{{ node.config.get('failure_threshold_pct', '') if node.config.get('failure_threshold_pct') is not none else '' }}">
      <label>Count</label>
      <input type="text" data-threshold-count style="width:80px;"
             placeholder="e.g. 10"
             value="{{ node.config.get('failure_threshold_count', '') if node.config.get('failure_threshold_count') is not none else '' }}">
    </div>
```

Keep the "Belongs to" (`data-procedure`) block, Severity, Description, and Item key rows unchanged.

- [ ] **Step 4: Remove the corresponding reads from `serialize()` (`logic_builder.html`)**

In the `type === 'test'` branch (~239-256), DELETE the `procTitle` / `tPct` / `tCnt` reads so the block becomes:

```javascript
          if (type === 'test') {
            node.config.procedure_id = (card.querySelector('[data-procedure]') || {}).value || null;
            node.config.severity = (card.querySelector('[data-severity]') || {}).value || 'medium';
            node.config.description_template = (card.querySelector('[data-desc]') || {}).value || '';
            node.config.item_key_column = (card.querySelector('[data-itemkey]') || {}).value || null;
          }
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/plane/test_logic_bands.py::test_test_node_card_has_no_procedure_identity_fields -q`
Expected: PASS.

- [ ] **Step 6: Confirm nothing else read those node fields for the demo**

Run: `grep -rn "failure_threshold\|config\\['title'\\]\|config.get('title'" examples/northwind-trading/controls/*/pipeline.yaml`
Expected: no per-node `failure_threshold_*` and no terminal `config.title` in the demo pipelines (so removing the UI fields is a no-op for the demo's auto-derived procedures — single-proc names fall back to the node `title`/`id`, thresholds to the control level). If any hit appears, note it in the ledger; it does not block this task (the YAML still parses; `_auto_procedure` still reads stored config).

- [ ] **Step 7: Gates + commit + push**

```bash
python -m pytest tests/plane -q && python -m ruff check . && python -m mypy uticen_lite
git add uticen_lite/plane/templates/partials/_pipe_node.html uticen_lite/plane/templates/logic_builder.html tests/plane/test_logic_bands.py
git commit -m "feat(plane): Test node carries step mechanics only — drop vestigial procedure fields"
git push -u origin HEAD
```

---

### Task 3: Keep a sole procedure's code empty (preserve 0036 heading byte-identity)

**Files:**
- Modify: `uticen_lite/plane/routes/pipeline.py` (`_procedure_context` code default ~701)
- Modify: `uticen_lite/plane/templates/logic_builder.html` (`serializeProcedures()` code default ~310)
- Test: `tests/plane/test_logic_bands.py`

**Interfaces:**
- Consumes: `effective_procedures(pipeline)` (already imported in `_procedure_context`).
- Produces: `_procedure_context(pipeline)["procedures"][i]["code"]` is `""` for a sole effective procedure with no author-defined code, and `P{i+1}` only when 2+ procedures exist; author-defined codes preserved. The JS `serializeProcedures()` mirrors this for the persisted value.

- [ ] **Step 1: Write the failing tests**

Add to `tests/plane/test_logic_bands.py`:

```python
def test_procedure_context_sole_procedure_code_empty():
    """A single auto-derived procedure shows an EMPTY code so the workpaper heading
    stays the legacy 'P1: title' form (learning 0036) — not 'P1'."""
    pipe = parse_pipeline({
        "nodes": [
            {"id": "src", "type": "import", "source_id": "s"},
            {"id": "t1", "type": "test", "inputs": ["src"],
             "config": {"conditions": [{"column": "a", "op": "not_empty"}]}},
        ],
    })
    ctx = _procedure_context(pipe)
    assert len(ctx["procedures"]) == 1
    assert ctx["procedures"][0]["code"] == ""


def test_procedure_context_multi_procedure_codes_numbered():
    """With 2+ procedures, auto codes are P1..Pn by position."""
    pipe = parse_pipeline({
        "nodes": [
            {"id": "src", "type": "import", "source_id": "s"},
            {"id": "t1", "type": "test", "inputs": ["src"],
             "config": {"conditions": [{"column": "a", "op": "not_empty"}]}},
            {"id": "t2", "type": "test", "inputs": ["src"],
             "config": {"conditions": [{"column": "a", "op": "not_empty"}]}},
        ],
    })
    ctx = _procedure_context(pipe)
    assert [p["code"] for p in ctx["procedures"]] == ["P1", "P2"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/plane/test_logic_bands.py::test_procedure_context_sole_procedure_code_empty -q`
Expected: FAIL — `assert ctx["procedures"][0]["code"] == ""` gets `"P1"`.

- [ ] **Step 3: Fix the display default (`routes/pipeline.py`)**

In `_procedure_context`, change the `code` line in the procedures dict from `"code": p.code or f"P{i + 1}",` to:

```python
                "code": p.code or (f"P{i + 1}" if len(eff) > 1 else ""),
```

- [ ] **Step 4: Fix the serialize default (`logic_builder.html`)**

In `serializeProcedures()`, change the `code` line from `code: (head.querySelector('[data-proc-code]') || {}).value || ('P' + (i + 1)),` to:

```javascript
          code: (head.querySelector('[data-proc-code]') || {}).value
                 || (heads.length > 1 ? 'P' + (i + 1) : ''),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/plane/test_logic_bands.py -q`
Expected: PASS (new tests + existing — `test_card_bands_groups_vms_by_procedure` still asserts `"P1"` for its 2-procedure fixture).

- [ ] **Step 6: Confirm no test pins a sole/auto procedure code of "P1"**

Run: `grep -rn "code.*== .\?P1\|'P1'\|\"P1\"" tests/plane tests/pipeline`
Expected: any hit is on a fixture with 2+ procedures or an explicit author code (still `"P1"`). If a single-auto-procedure test asserts `"P1"`, update it to `""` (the new, byte-identical behavior) and note it in the ledger.

- [ ] **Step 7: Run the render byte-identity guard (no change expected)**

Run: `python -m pytest tests/render/test_procedure_render.py -q`
Expected: PASS — `test_lone_auto_code_empty_heading_is_legacy_form` already pins that `code=""` renders `P1: title`; this task keeps the Builder from ever promoting a sole procedure to `code="P1"`.

- [ ] **Step 8: Gates + commit + push**

```bash
python -m pytest tests/plane tests/render -q && python -m ruff check . && python -m mypy uticen_lite
git add uticen_lite/plane/routes/pipeline.py uticen_lite/plane/templates/logic_builder.html tests/plane/test_logic_bands.py
git commit -m "fix(plane): keep a sole procedure's code empty to preserve 0036 heading byte-identity"
git push -u origin HEAD
```

---

### Task 4: Populate Finance.GL.1 procedure narratives (showcase the header)

**Files:**
- Modify: `examples/northwind-trading/controls/manual-je-review/pipeline.yaml` (the `procedures:` array)
- Test: `tests/examples/test_northwind.py` (the `gl1_procs` assertions block ~114-135)

**Interfaces:**
- Consumes: `compile.py:128` uses `proc.narrative or tests[0].narrative` (bundle) and `run_service` uses `proc.narrative` directly (local) — populating `ProcedureDef.narrative` drives BOTH consistently.
- Produces: the bundle workpaper `gl1["workpaper"]["procedures"][*]["narrative"]` equals the authored procedure narratives.

- [ ] **Step 1: Write the failing test**

In `tests/examples/test_northwind.py`, after the existing P1/P2 `failed` assertions (~135), add:

```python
    # Each procedure carries its own authored narrative (shown in the procedure
    # header + workpaper), distinct from the per-step node narratives.
    assert p1["narrative"] == (
        "Independent review (segregation of duties): every material manual journal "
        "entry must be reviewed by someone other than its preparer. Flags entries a "
        "preparer reviewed themselves."
    ), p1["narrative"]
    assert p2["narrative"] == (
        "Authorization evidence: every material manual journal entry must have an "
        "independent reviewer recorded. Flags entries with no reviewer assigned."
    ), p2["narrative"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/examples/test_northwind.py -q`
Expected: FAIL — the narratives currently fall back to the terminal node narratives (e.g. "P1 — Segregation of duties: flag material…"), not the authored procedure text.

- [ ] **Step 3: Add the narratives to the YAML procedures**

In `examples/northwind-trading/controls/manual-je-review/pipeline.yaml`, set the `procedures:` array to:

```yaml
procedures:
  - id: p1
    code: P1
    name: Independent Review (SoD)
    assertion: Segregation of duties
    narrative: >-
      Independent review (segregation of duties): every material manual journal entry
      must be reviewed by someone other than its preparer. Flags entries a preparer
      reviewed themselves.
    position: 0
  - id: p2
    code: P2
    name: Reviewer Assigned
    assertion: Authorization / approval evidence
    narrative: >-
      Authorization evidence: every material manual journal entry must have an
      independent reviewer recorded. Flags entries with no reviewer assigned.
    position: 1
```

(Keep the existing per-step node narratives on `sod` and `review` unchanged — they describe the step, not the procedure.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/examples/test_northwind.py -q`
Expected: PASS. The YAML `>-` block folds to a single line with no trailing newline, matching the asserted strings.

- [ ] **Step 5: Confirm the contract gate stays green**

Run: `python -m pytest tests/test_contract_export.py tests/schema -q`
Expected: PASS — procedure narrative is additive content in the already-unbounded `workpaper.procedures` array; no schema/shape change.

- [ ] **Step 6: Commit + push**

```bash
git add examples/northwind-trading/controls/manual-je-review/pipeline.yaml tests/examples/test_northwind.py
git commit -m "demo: author Finance.GL.1 procedure narratives so the header drives the workpaper"
git push -u origin HEAD
```

---

### Task 5: Browser e2e — header owns identity, node is mechanics-only (learnings 0012 + 0037)

**Files:**
- Modify: `tests/e2e/test_smoke.py` (`test_author_run_export_smoke`, ~109-203)

**Interfaces:**
- Consumes: the existing smoke flow already creates a control, opens the Builder, clicks `#proc-add`, and fills `data-proc-code/name/assert` on `[data-proc-section]`.
- Produces: end-to-end proof that (a) the Test node card has no procedure-identity fields, (b) the procedure header has a narrative field that persists across reload via the app's own save path.

- [ ] **Step 1: Add the node-absence + narrative-persistence assertions**

In `tests/e2e/test_smoke.py`, inside `test_author_run_export_smoke`, after the Builder is loaded with the authored nodes and BEFORE/AROUND the "Add a procedure SECTION" block (~169-184):

After the new section is created and its code/name/assert are filled (~184), also fill the narrative and assert the node has no procedure-identity fields:

```python
    # The procedure header owns the narrative (Unit 1); the Test node has no
    # procedure-identity fields (Unit 2).
    new_section.locator("[data-proc-narrative]").fill("Reviewer must be independent of the preparer.")
    expect(page.locator('[data-node="tst"] [data-proc-title]')).to_have_count(0)
    expect(page.locator('[data-node="tst"] [data-threshold-pct]')).to_have_count(0)
    expect(page.locator('[data-node="tst"] [data-threshold-count]')).to_have_count(0)
```

- [ ] **Step 2: Assert the narrative persists across reload (write path — learning 0037)**

After the existing Save + reload of the Builder (~193-202), add an assertion that the procedure narrative survived the server round-trip:

```python
    expect(
        page.locator(f'[data-proc-head][data-proc-id="{pid}"] [data-proc-narrative]')
    ).to_have_value("Reviewer must be independent of the preparer.")
```

(The narrative is server-persisted via the same Save/autosave that already persists code/name/assert in this test; reload-and-assert-value is the write-path assertion — no `setItem` / state injection.)

- [ ] **Step 3: Run the e2e**

Run: `python -m pytest tests/e2e -m browser -q`
Expected: PASS. If a locator/strict-mode failure appears, read it — do not dismiss as flaky (learning 0012); fix the locator or the markup so the assembled post-swap DOM matches.

- [ ] **Step 4: Commit + push**

```bash
git add tests/e2e/test_smoke.py
git commit -m "test(e2e): procedure header owns narrative + persists; Test node has no procedure fields"
git push -u origin HEAD
```

---

### Task 6: Full-suite + gates sweep

**Files:** none (verification only).

- [ ] **Step 1: Run the full unit suite pristine**

Run: `python -m pytest -q`
Expected: PASS with no new warnings. Pay attention to `tests/render`, `tests/plane`, `tests/examples`, `tests/test_contract_export.py`, `tests/schema`.

- [ ] **Step 2: Lint + type gates**

Run: `python -m ruff check . && python -m mypy uticen_lite`
Expected: clean.

- [ ] **Step 3: Confirm no bundle drift**

Run: `git diff --stat main..HEAD -- contract/ uticen_lite/schema/`
Expected: EMPTY — no change to `contract/bundle.schema.json` or the bundle schema (cardinal rule; the change is render+store-only).

- [ ] **Step 4: If anything failed, fix and re-run; otherwise the branch is ready for the whole-branch review.**

---

## Self-review (plan vs. spec)

- **Spec Unit 1 (header narrative)** → Task 1 (view-model thread 0038, template, JS serialize, CSS). ✓
- **Spec Unit 2 (node = step mechanics)** → Task 2 (remove two rows + serialize reads). ✓
- **Spec Unit 3 (sole code="" — display + serialize)** → Task 3 (both defaults + tests + 0036 render guard re-run). ✓
- **Spec Unit 4 (demo narratives + test)** → Task 4 (YAML + `test_northwind` + contract gate). ✓
- **Spec Testing strategy (e2e 0012/0037; render byte-identity; demo)** → Tasks 3, 4, 5; full sweep Task 6. ✓
- **Global constraints (cardinal/0015, 0036, 0038, Pyodide, gates)** → Global Constraints block + Task 6 bundle-drift check. ✓
- Type/name consistency: `data-proc-narrative` / `proc-narr` / `narrative` used consistently across template, JS, view-model, and tests. No placeholders.
