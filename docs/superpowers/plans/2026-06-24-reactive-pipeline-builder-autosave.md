# Reactive Pipeline Builder Autosave Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make pipeline-builder edits autosave in place so adding a condition, changing narrative text, or removing a node updates the builder without snapping the user back to the top of the page.

**Architecture:** Keep the server-rendered builder and store-backed graph as the source of truth. Add a dedicated autosave mode for the existing builder POST route, then have the builder template call that mode asynchronously and swap only the cards fragment back into place. The explicit Save pipeline path can remain as the legacy redirecting submit, but routine edits should use the new in-place autosave path.

**Tech Stack:** FastAPI, Jinja templates, HTMX-compatible HTML responses, pytest, pytest-playwright.

---

### Task 1: Add an autosave response mode to the builder save route

**Files:**
- Modify: `uticen_lite/plane/routes/pipeline.py:844-875`
- Test: `tests/plane/test_pipeline_save.py`

- [ ] **Step 1: Write the failing test**

```python
def test_builder_autosave_returns_cards_fragment_without_redirect(client):
    _make_source(client, "autosave_src", b"emp_id,status\nE1,active\n")
    graph = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "autosave_src"},
            {"id": "tst", "type": "test", "inputs": ["imp"], "config": {
                "logic": "all",
                "severity": "medium",
                "item_key_column": "emp_id",
                "conditions": [{"column": "status", "op": "eq", "value": "active"}],
            }},
        ]
    }
    client.post("/controls", data={"id": "AS1", "title": "Autosave", "objective": "o", "narrative": "n"},
                follow_redirects=False)

    resp = client.post(
        "/controls/AS1/logic/builder",
        data={"pipeline_json": json.dumps(graph), "autosave": "1"},
        headers={"X-Builder-Autosave": "1"},
        follow_redirects=False,
    )

    assert resp.status_code == 200
    assert "pipe-cards" in resp.text
    assert 'data-node="tst"' in resp.text
    assert resp.headers.get("location") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/plane/test_pipeline_save.py::test_builder_autosave_returns_cards_fragment_without_redirect -v`
Expected: FAIL because the builder POST still redirects.

- [ ] **Step 3: Write minimal implementation**

```python
autosave = form.get("autosave") == "1" or request.headers.get("X-Builder-Autosave") == "1"
...
if autosave:
    ctx = _editor_context(request, conn, root, control_id, for_builder=True)
    ctx["active"] = "logic"
    ctx["logic_tab"] = "builder"
    return templates.TemplateResponse(
        request,
        "partials/_pipe_cards.html",
        ctx,
    )
return RedirectResponse(f"/controls/{control_id}/logic/builder", status_code=303)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/plane/test_pipeline_save.py::test_builder_autosave_returns_cards_fragment_without_redirect -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add uticen_lite/plane/routes/pipeline.py tests/plane/test_pipeline_save.py
git commit -m "feat(plane): add builder autosave response mode"
```

### Task 2: Make the builder template autosave in place instead of submitting the full page

**Files:**
- Modify: `uticen_lite/plane/templates/logic_builder.html:135-438`
- Modify: `uticen_lite/plane/templates/partials/_pipe_cards.html:1-36`
- Modify: `uticen_lite/plane/templates/partials/_pipe_node.html:58-203`

- [ ] **Step 1: Write the failing test**

```python
def test_builder_renders_autosave_hook_and_status_region(client, seeded_pipeline_control):
    html = client.get(f"/controls/{seeded_pipeline_control}/logic/builder").text
    assert "autosave" in html.lower()
    assert "Saving" in html or "Saved" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/plane/test_pipeline_editor.py::test_builder_renders_autosave_hook_and_status_region -v`
Expected: FAIL because the template still uses full-form submit behavior.

- [ ] **Step 3: Write minimal implementation**

```javascript
function autosaveGraph() {
  serialize();
  jsonField.value = JSON.stringify(graph);
  var form = document.getElementById('pipeline-form');
  var body = new FormData(form);
  body.set('pipeline_json', jsonField.value);
  body.set('autosave', '1');
  return fetch(form.action, {
    method: 'POST',
    headers: {'X-Builder-Autosave': '1', 'X-Requested-With': 'fetch'},
    body: body,
    credentials: 'same-origin',
  }).then(function (resp) {
    return resp.text().then(function (html) {
      document.getElementById('pipe-cards').innerHTML = html;
      bindCards(document.getElementById('pipe-cards'));
    });
  });
}
```

Add the helper to the add-condition/remove-node/narrative change paths, and keep the old submit handler only for the explicit Save pipeline button.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/plane/test_pipeline_editor.py::test_builder_renders_autosave_hook_and_status_region -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add uticen_lite/plane/templates/logic_builder.html uticen_lite/plane/templates/partials/_pipe_cards.html uticen_lite/plane/templates/partials/_pipe_node.html
git commit -m "feat(plane): make pipeline builder edits autosave in place"
```

### Task 3: Preserve scroll position and inline errors in the browser flow

**Files:**
- Modify: `tests/e2e/test_smoke.py:116-172`
- Modify: `tests/e2e/test_smoke.py:133-157`

- [ ] **Step 1: Write the failing test**

```python
page.goto(base + "/controls/sod/logic/builder")
page.locator('[data-node="tst"]').scroll_into_view_if_needed()
before = page.evaluate("window.scrollY")
page.locator('[data-node="tst"] [data-add-cond]').click()
expect(page.locator('[data-node="tst"] [data-cond]')).to_have_count(1)
after = page.evaluate("window.scrollY")
assert after >= before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/e2e/test_smoke.py -m browser -k author_run_export_smoke -v`
Expected: FAIL because the current builder navigation jumps back to the top.

- [ ] **Step 3: Write minimal implementation**

```python
# In the browser test, stop expecting full navigation after add-condition clicks.
# Assert the same page stays loaded and the new condition row appears in place.
with page.expect_response(lambda resp: resp.request.method == "POST" and "/logic/builder" in resp.url):
    test_card.locator("[data-add-cond]").click()
expect(page).to_have_url(base + "/controls/sod/logic/builder")
assert page.evaluate("window.scrollY") >= before
```

Keep the existing inline-error assertion path for invalid graphs by checking the rejected autosave response still renders node errors in place.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/e2e/test_smoke.py -m browser -k author_run_export_smoke -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_smoke.py
git commit -m "test(e2e): cover scroll-stable pipeline autosave"
```

### Task 4: Run the focused plane and browser suites

**Files:**
- No code changes

- [ ] **Step 1: Run the focused plane tests**

Run: `python -m pytest -q tests/plane/test_pipeline_save.py tests/plane/test_pipeline_editor.py -k "autosave or builder or pipeline" -v`
Expected: PASS.

- [ ] **Step 2: Run the browser smoke lane**

Run: `python -m pytest -q tests/e2e -m browser`
Expected: PASS.

- [ ] **Step 3: Commit verification cleanups if needed**

```bash
git status --short
```

Expected: no unexpected files beyond the intended code and test edits.
