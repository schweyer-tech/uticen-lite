# Procedure header title layout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the Logic Builder's procedure section header into a titled card — a big editable name with a focus-pencil, a labeled Assertion field with an explanatory tooltip, a readable "Fail if … % or … items" threshold, and the narrative as the full-width bottom row.

**Architecture:** Purely presentational. The `.proc-head` summary is restructured from one wrapping flex row into three rows (title / fields / narrative). All `data-proc-*` attributes are unchanged, so `serializeProcedures()` (which reads by attribute, not position) is unaffected — no route/view-model/graph/bundle change.

**Tech Stack:** Jinja2 partial (`_pipe_cards.html`), vanilla JS (`logic_builder.html`), CSS (`app.css`), pytest + FastAPI TestClient (plane), Playwright (e2e).

## EXECUTION RULES

- Never ask the user for permission to continue between tasks. Execute the full plan start to finish without interruption.
- After every `git commit`, push: `git push -u origin HEAD`.
- On an unresolvable error after 2–3 attempts: note it in the progress ledger and skip to the next task.

## Global Constraints

- **Purely presentational:** NO change to `contract/bundle.schema.json`, `schema_version`, routes, view-models (`_procedure_context`/`_card_bands`), or the serialized graph shape. All `data-proc-*` attribute names are unchanged (learnings 0001, 0015).
- **CSS specificity (learning 0032):** the big name-title input rule MUST out-specify the global `input[type="text"]` block declared later in `app.css` (qualify as `.proc-head input.proc-name-title`, specificity (0,2,1) > base (0,1,1)) — or the title silently reverts to the base 13px. Verify the rendered font-size in a real browser (e2e teeth-check).
- **Tokens / modifier classes (learning 0005):** route every color through `var(--token)`; add component-scoped classes, never mutate a shared base rule.
- **No-toggle invariants:** `.proc-dot` stays a SIBLING of `.proc-head` (it is the collapse-click target the e2e drives — moving it inside `.proc-head` breaks `test_builder_collapse_and_section_insert`). The name/narrative inputs stay inside `.proc-head` so they inherit the existing keydown no-toggle guard (`logic_builder.html:630`); the pencil click handler additionally `preventDefault()`s so the click cannot toggle the `<details>`.
- ruff `py311`, line length 100; tests pristine; `python -m ruff check .` + `python -m mypy controlflow_sdk` clean.

---

### Task 1: Header structure — 3-row markup, JS mirror, focus pencil

**Files:**
- Modify: `controlflow_sdk/plane/templates/partials/_pipe_cards.html` (the `proc-head` span, lines ~61-71)
- Modify: `controlflow_sdk/plane/templates/logic_builder.html` (`newProcedureSection()` innerHTML ~347-356; add a `[data-proc-name-edit]` click handler near the existing `#pipe-cards` click delegation ~367)
- Test: `tests/plane/test_logic_bands.py`

**Interfaces:**
- Consumes: `band.proc.{code,name,assertion,failure_threshold_pct,failure_threshold_count,narrative}` (unchanged view-model).
- Produces: a `.proc-head` containing `.proc-title-row` (code badge · `proc-name-title` input · `data-proc-name-edit` pencil · `data-proc-del`), `.proc-fields-row` (Assertion label + `.proc-help` tooltip + assertion input + "Fail if … % or … items" threshold), `.proc-narrative-row` (Narrative label + `data-proc-narrative`). Same `data-proc-*` attributes as before.

- [ ] **Step 1: Write the failing test**

Add to `tests/plane/test_logic_bands.py` (reuses the existing `_seed_with_procedure` helper):

```python
def test_builder_get_renders_procedure_title_layout(client):
    """The procedure header renders as a titled card: a big title-styled name input
    with a pencil, an Assertion label + tooltip, the 'Fail if' threshold, and a
    Narrative label — all data-proc-* attributes unchanged."""
    _seed_with_procedure(client)
    page = client.get("/controls/c1/logic/builder").text
    # Name is the big title input + a focus pencil.
    assert "proc-name-title" in page
    assert "data-proc-name-edit" in page
    # Assertion label + explanatory tooltip copy.
    assert "audit assertion this procedure verifies" in page
    # Threshold relabel + narrative label.
    assert "Fail if" in page
    assert "proc-narrative-row" in page
    # Attributes the serializer reads are still present.
    for attr in ("data-proc-code", "data-proc-name", "data-proc-assert",
                 "data-proc-pct", "data-proc-count", "data-proc-narrative"):
        assert attr in page
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/plane/test_logic_bands.py::test_builder_get_renders_procedure_title_layout -v`
Expected: FAIL — `proc-name-title` / `data-proc-name-edit` / the tooltip copy not in the page.

- [ ] **Step 3: Restructure the header markup (`_pipe_cards.html`)**

Replace the `proc-head` span body (lines ~61-71, from `<span class="proc-head" ...>` through its closing `</span>`) with:

```html
    <span class="proc-head" data-proc-head data-proc-id="{{ band.proc.id }}">
      <div class="proc-title-row">
        <input class="proc-in proc-code-badge" data-proc-code value="{{ band.proc.code }}" placeholder="P1" aria-label="Code">
        <input class="proc-in proc-name-title" type="text" data-proc-name value="{{ band.proc.name }}" placeholder="Procedure name" aria-label="Procedure name">
        <button type="button" class="proc-name-pencil" data-proc-name-edit aria-label="Edit procedure name"></button>
        <button type="button" class="btn btn-sm btn-ghost proc-del-btn" data-proc-del aria-label="Remove procedure">✕</button>
      </div>
      <div class="proc-fields-row">
        <label class="proc-field-label">Assertion
          <span class="proc-help" tabindex="0" role="img" aria-label="What is an assertion?"
                title="The audit assertion this procedure verifies — the specific claim it proves about the control (e.g. 'Segregation of duties', 'Authorization', 'Completeness', 'Existence'). Shown as the procedure's subtitle in the workpaper.">&#9432;</span>
        </label>
        <input class="proc-in proc-assert-in" data-proc-assert value="{{ band.proc.assertion }}" placeholder="e.g. Segregation of duties" aria-label="Assertion">
        <span class="proc-threshold">
          <span class="proc-field-label">Fail if</span>
          <input class="proc-in proc-thr-in" data-proc-pct value="{{ band.proc.failure_threshold_pct if band.proc.failure_threshold_pct is not none else '' }}" placeholder="&#8212;" aria-label="Threshold percent">
          <span class="proc-field-label">% or</span>
          <input class="proc-in proc-thr-in" data-proc-count value="{{ band.proc.failure_threshold_count if band.proc.failure_threshold_count is not none else '' }}" placeholder="&#8212;" aria-label="Threshold count">
          <span class="proc-field-label">items</span>
        </span>
      </div>
      <div class="proc-narrative-row">
        <label class="proc-field-label">Narrative</label>
        <textarea class="proc-in proc-narr" data-proc-narrative rows="2"
                  placeholder="What this procedure tests and why"
                  aria-label="Procedure narrative">{{ band.proc.narrative }}</textarea>
      </div>
    </span>
```

(Leave the `<span class="proc-dot" ...>` line above it untouched — it stays a sibling of `.proc-head`.)

- [ ] **Step 4: Mirror the markup in `newProcedureSection()` (`logic_builder.html`)**

Replace the proc-head innerHTML string (lines ~347-355, from the `data-proc-code` input through the `data-proc-narrative` textarea, i.e. the strings between `data-proc-id="' + pid + '">'` and `'</span></summary>'`) with the same 3-row structure as JS string concatenation:

```javascript
        '<div class="proc-title-row">' +
        '<input class="proc-in proc-code-badge" data-proc-code placeholder="P1" aria-label="Code">' +
        '<input class="proc-in proc-name-title" type="text" data-proc-name placeholder="Procedure name" aria-label="Procedure name">' +
        '<button type="button" class="proc-name-pencil" data-proc-name-edit aria-label="Edit procedure name"></button>' +
        '<button type="button" class="btn btn-sm btn-ghost proc-del-btn" data-proc-del aria-label="Remove procedure">✕</button>' +
        '</div>' +
        '<div class="proc-fields-row">' +
        '<label class="proc-field-label">Assertion ' +
        '<span class="proc-help" tabindex="0" role="img" aria-label="What is an assertion?" ' +
        'title="The audit assertion this procedure verifies — the specific claim it proves about the control (e.g. \'Segregation of duties\', \'Authorization\', \'Completeness\', \'Existence\'). Shown as the procedure\'s subtitle in the workpaper.">ⓘ</span>' +
        '</label>' +
        '<input class="proc-in proc-assert-in" data-proc-assert placeholder="e.g. Segregation of duties" aria-label="Assertion">' +
        '<span class="proc-threshold">' +
        '<span class="proc-field-label">Fail if</span>' +
        '<input class="proc-in proc-thr-in" data-proc-pct placeholder="—" aria-label="Threshold percent">' +
        '<span class="proc-field-label">% or</span>' +
        '<input class="proc-in proc-thr-in" data-proc-count placeholder="—" aria-label="Threshold count">' +
        '<span class="proc-field-label">items</span>' +
        '</span></div>' +
        '<div class="proc-narrative-row">' +
        '<label class="proc-field-label">Narrative</label>' +
        '<textarea class="proc-in proc-narr" data-proc-narrative rows="2" ' +
        'placeholder="What this procedure tests and why" ' +
        'aria-label="Procedure narrative"></textarea>' +
        '</div>' +
        '</span></summary>' +
```

(The line `sec.querySelector('[data-proc-code]').value = code;` after the innerHTML assignment still works — `data-proc-code` is unchanged.)

- [ ] **Step 5: Add the focus-pencil click handler (`logic_builder.html`)**

In the existing `cardsRoot.addEventListener('click', …)` handler (the one that handles `#proc-add` and `[data-proc-del]`, starting ~line 367), add a branch BEFORE the `#proc-add` branch (so a pencil click is handled and returns):

```javascript
      var pencil = e.target.closest('[data-proc-name-edit]');
      if (pencil) {
        e.preventDefault();        // don't toggle the <details>
        e.stopPropagation();
        var head = pencil.closest('[data-proc-head]');
        var nameInput = head && head.querySelector('[data-proc-name]');
        if (nameInput) { nameInput.focus(); }
        return;
      }
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python -m pytest tests/plane/test_logic_bands.py -q`
Expected: PASS (new test + existing band/proc tests — attributes unchanged).

- [ ] **Step 7: Gates + commit + push**

```bash
python -m ruff check . && python -m mypy controlflow_sdk
git add controlflow_sdk/plane/templates/partials/_pipe_cards.html controlflow_sdk/plane/templates/logic_builder.html tests/plane/test_logic_bands.py
git commit -m "feat(plane): procedure header as a titled card — big name + pencil, Assertion label/tooltip, narrative row"
git push -u origin HEAD
```

---

### Task 2: Styling + browser e2e (learnings 0032 + 0012 + 0005)

**Files:**
- Modify: `controlflow_sdk/plane/static/app.css` (modify the existing `.proc-head` rule ~696; add the new row/title/pencil/help/label rules after the existing `.proc-head .proc-narr` rule)
- Modify: `tests/e2e/test_smoke.py` (`test_author_run_export_smoke`)

**Interfaces:**
- Consumes: the markup classes from Task 1 (`.proc-title-row`, `.proc-name-title`, `.proc-name-pencil`, `.proc-fields-row`, `.proc-help`, `.proc-field-label`, `.proc-threshold`, `.proc-narrative-row`).
- Produces: a vertically-stacked titled header; the name input renders at heading size (≈20px), the pencil focuses the name input.

- [ ] **Step 1: Make `.proc-head` a column + add the row/title/pencil/help styles (`app.css`)**

Change the existing `.proc-head` rule (currently `display: inline-flex; flex-wrap: wrap; gap: 8px; align-items: center; flex: 1;`) to a column:

```css
.proc-head { display: flex; flex-direction: column; align-items: stretch; gap: 8px; flex: 1; }
```

Then add, after the existing `.proc-head .proc-narr { … }` rule:

```css
.proc-title-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.proc-fields-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.proc-narrative-row { display: flex; flex-direction: column; gap: 4px; }

/* Code badge: compact, centered. */
.proc-head .proc-code-badge {
  width: 48px; text-align: center; font-weight: 600; font-family: var(--font-mono);
}

/* Name as an in-place heading. The `input.` qualifier raises specificity to (0,2,1)
   so it beats the later global `input[type="text"]` block (0,1,1) — learning 0032. */
.proc-head input.proc-name-title {
  flex: 1; min-width: 200px; margin: 0;
  font-family: var(--font-sans); font-size: 20px; font-weight: 600;
  color: var(--text-primary); background: transparent;
  border: 1px solid transparent; border-radius: var(--radius-input); padding: 4px 8px;
}
.proc-head input.proc-name-title:hover { border-color: var(--border-default); }
.proc-head input.proc-name-title:focus {
  outline: none; background: var(--bg-input);
  border-color: var(--accent-primary); box-shadow: 0 0 0 3px var(--accent-muted);
}

/* Pencil — mirrors .control-title-pencil. */
.proc-name-pencil {
  display: inline-flex; align-items: center; justify-content: center;
  width: 24px; height: 24px; padding: 0;
  border: 1px solid var(--border-strong); border-radius: 999px;
  background: transparent; color: var(--text-secondary); cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}
.proc-name-pencil:hover { color: var(--accent-primary); border-color: var(--accent-primary); }
.proc-name-pencil::before { content: '✎'; font-size: 12px; }

/* Field labels + the assertion help tooltip. */
.proc-field-label {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 12px; color: var(--text-secondary);
}
.proc-help {
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 13px; color: var(--text-tertiary); cursor: help;
}
.proc-help:hover, .proc-help:focus { color: var(--accent-primary); outline: none; }
.proc-threshold { display: inline-flex; align-items: center; gap: 6px; }
.proc-head .proc-thr-in { width: 56px; }
```

(Leave `.proc-head .proc-narr` intact for the textarea's resize/min-height; it now lives in `.proc-narrative-row`.)

- [ ] **Step 2: Add the e2e teeth-check + pencil assertion (`tests/e2e/test_smoke.py`)**

In `test_author_run_export_smoke`, after the new procedure section is created and its `data-proc-name` is filled (around where the section's code/name/assert are set), add:

```python
    # 0032 teeth-check: the name renders at heading size (not the base 13px input).
    expect(new_section.locator("[data-proc-name]")).to_have_css("font-size", "20px")
    # The pencil focuses the name input (no toggle, no separate form).
    new_section.locator("[data-proc-name-edit]").click()
    expect(new_section.locator("[data-proc-name]")).to_be_focused()
```

- [ ] **Step 3: Run the browser e2e**

Run: `python -m pytest tests/e2e -m browser -q` (after `python -m playwright install chromium` if needed)
Expected: PASS — the existing `[data-proc-name]`/`[data-proc-assert]`/`[data-proc-count]`/`[data-proc-narrative]` fills still resolve (unchanged attributes), the name font-size is `20px`, and the pencil focuses the name. If a locator fails, read it (learning 0012) — do not dismiss as flaky.

- [ ] **Step 4: Gates + commit + push**

```bash
python -m ruff check .
git add controlflow_sdk/plane/static/app.css tests/e2e/test_smoke.py
git commit -m "feat(plane): style the procedure header card + e2e (0032 title font-size, pencil focus)"
git push -u origin HEAD
```

---

### Task 3: Full-suite + gates sweep

**Files:** none (verification only).

- [ ] **Step 1: Full unit suite**

Run: `python -m pytest -q`
Expected: PASS, pristine (no new warnings).

- [ ] **Step 2: Lint + type gates**

Run: `python -m ruff check . && python -m mypy controlflow_sdk`
Expected: clean.

- [ ] **Step 3: Confirm no bundle/route drift**

Run: `git diff --stat origin/main..HEAD -- contract/ controlflow_sdk/schema/ controlflow_sdk/plane/routes/`
Expected: EMPTY — the change is presentational (templates/JS/CSS) only.

- [ ] **Step 4: If anything failed, fix and re-run; else the branch is ready for the whole-branch review.**

---

## Self-review (plan vs. spec)

- **Spec Unit 1 (markup)** → Task 1 Steps 3 (template). ✓
- **Spec Unit 2 (JS mirror + pencil handler)** → Task 1 Steps 4-5. ✓
- **Spec Unit 3 (CSS)** → Task 2 Step 1, with the 0032-qualified title selector + 0005 tokens. ✓
- **Spec testing (plane render; e2e 0012 + 0032 font-size teeth-check; pencil focus)** → Task 1 test + Task 2 e2e; full sweep Task 3. ✓
- **Constraints (presentational/no contract/route/view-model change; dot stays sibling; pencil preventDefault; tokens)** → Global Constraints + Task 3 drift check. ✓
- Names consistent across template, JS, CSS, tests (`proc-name-title`, `data-proc-name-edit`, `proc-help`, `proc-fields-row`, `proc-narrative-row`, `proc-threshold`). No placeholders.
