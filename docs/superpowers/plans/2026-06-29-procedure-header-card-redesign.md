# Procedure-header card redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the Logic Builder procedure-header `<summary>` into a clean 3-tier card (identity bar · settings strip · narrative) with peer labels, an always-visible tolerance control, a left accent stripe, and quiet hover icons — keeping every field, behavior, and `data-proc-*` hook.

**Architecture:** Pure `plane/` authoring-UI change across the two render sites that produce the header — the Jinja template (`partials/_pipe_cards.html`) and the client JS string builder (`logic_builder.html` → `newProcedureSection()`) — plus the shared CSS (`static/app.css`). The floating `.proc-dot` is replaced by a left border stripe on the card and a real `.band-caret` toggle element (a robust click target for users and e2e). No server/Python logic, save payload, or bundle/contract changes.

**Tech Stack:** FastAPI + Jinja2 + HTMX, vanilla JS, hand-rolled CSS with design tokens; pytest + Starlette TestClient + Playwright (`-m browser`).

## Global Constraints

- Python ≥ 3.11; ruff target `py311`, line-length 100. Keep `python -m pytest -q` pristine (no stray warnings), `python -m ruff check .` and `python -m mypy controlflow_sdk` green.
- **Preserve every save hook** inside `[data-proc-head]`: `data-proc-id`, `[data-proc-code]`, `[data-proc-name]`, `[data-proc-assert]`, `[data-proc-narrative]`, `[data-proc-pct]`, `[data-proc-count]`, `[data-proc-name-edit]`, `[data-proc-del]`. `serializeProcedures()` reads by attribute, not by structural class (learning 0014/0038).
- **Both render sites must stay byte-consistent** — the Jinja template and `newProcedureSection()` must emit the same structure (learning 0038/0036).
- **No apostrophes in the assertion-help tooltip** so the hand-concatenated JS string is escape-safe (learning 0040). Same reworded copy in both sites.
- Route all colors through `var(--token)`; the per-procedure stripe color is emitted inline; works in **both** light and dark themes (learning 0005).
- Keep the `input.proc-name-title` selector specificity `(0,2,1)` so it beats the global `input[type="text"]` block (learning 0032).
- No bundle/contract surface: thresholds are render+store only (learning 0015). `contract/bundle.schema.json` untouched.
- **Every commit** ends with this trailer and is immediately followed by `git push -u origin HEAD`:

  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_013JpkfZUEVoutkGJAYHqrKJ
  ```

---

## EXECUTION RULES

- Never ask the user for permission to continue between tasks.
- Execute the full plan start to finish without interruption.
- On an unresolvable error after 2–3 attempts: note it and skip to the next task.
- After every `git commit`, run `git push -u origin HEAD`.

---

## File Structure

- `controlflow_sdk/plane/templates/partials/_pipe_cards.html` — Jinja: the Inputs band summary (add caret) + the per-procedure `<details>`/`<summary>` (3-tier restructure, inline stripe color). **Touched in Task 1.**
- `controlflow_sdk/plane/static/app.css` — the `.band-*`/`.proc-*` block (~lines 700–799): carets, stripe, identity bar, code chip, name (specificity preserved), icon buttons, settings strip, peer labels, tolerance row, narrative. **Touched in Task 1.**
- `controlflow_sdk/plane/templates/logic_builder.html` — `newProcedureSection()` JS builder (~lines 337–382): mirror the new structure. **Touched in Task 2.**
- `tests/plane/test_logic_bands.py` — structural render test (migrate "Fail if"→"Tolerance", narrative hook). **Touched in Task 1.**
- `tests/e2e/test_smoke.py` — collapse interaction (`.proc-dot`→`.band-caret`) + a computed-style teeth-check. **Touched in Task 1 (collapse) + Task 3 (teeth-check).**
- `tests/e2e/test_multi_procedure.py` — add-procedure structural-parity + zero-`pageerror` assertion. **Touched in Task 2.**

---

## Task 1: Restructure the server-rendered procedure header (markup + CSS)

**Files:**
- Modify: `controlflow_sdk/plane/templates/partials/_pipe_cards.html`
- Modify: `controlflow_sdk/plane/static/app.css` (the `.band-*`/`.proc-*` block, ~700–799)
- Test: `tests/plane/test_logic_bands.py` (`test_builder_get_renders_procedure_title_layout`)
- Test: `tests/e2e/test_smoke.py` (collapse step: `.proc-dot` → `.band-caret`)

**Interfaces:**
- Produces (structure both later tasks/tests rely on): each procedure renders as
  `<details class="proc-section" ... style="border-left-color:<color>">` →
  `<summary class="band-head proc-head-row"><span class="band-caret" aria-hidden="true">▸</span><span class="proc-head" data-proc-head data-proc-id="<id>"> …three tiers… </span></summary>`.
  Tiers: `.proc-identity` (code chip + name + pencil + spacer + delete), `.proc-settings` (`.proc-field.proc-field-assert` + `.proc-field.proc-field-tol`), `.proc-field.proc-field-narr`.
- The Inputs band summary gains a leading `<span class="band-caret" aria-hidden="true">▸</span>`.
- Removed classes (no longer emitted anywhere): `proc-dot`, `proc-title-row`, `proc-fields-row`, `proc-narrative-row`, `proc-threshold`, `proc-code-badge`. New: `band-caret`, `proc-identity`, `proc-identity-spacer`, `proc-code-chip`, `proc-settings`, `proc-field`, `proc-field-assert`, `proc-field-tol`, `proc-field-narr`, `proc-tol-row`, `proc-tol-op`, `proc-tol-hint`. Kept: `proc-name-title`, `proc-name-pencil`, `proc-del-btn`, `proc-assert-in`, `proc-thr-in`, `proc-narr`, `proc-field-label`, `proc-help`, `proc-in`.

- [ ] **Step 1: Update the structural render test to the new expectations (failing test)**

In `tests/plane/test_logic_bands.py`, replace `test_builder_get_renders_procedure_title_layout` (lines 249–266) with:

```python
def test_builder_get_renders_procedure_title_layout(client):
    """The procedure header renders as a 3-tier card: a big title-styled name input
    with a pencil, an Assertion label + tooltip, a 'Tolerance' peer label, and a
    Narrative label — all data-proc-* attributes unchanged."""
    _seed_with_procedure(client)
    page = client.get("/controls/c1/logic/builder").text
    # Name is the big title input + a focus pencil.
    assert "proc-name-title" in page
    assert "data-proc-name-edit" in page
    # Assertion label + explanatory tooltip copy (reworded, apostrophe-free).
    assert "audit assertion this procedure verifies" in page
    # Threshold relabel ("Tolerance" peer label) + a real caret toggle element.
    assert "Tolerance" in page
    assert "Fail if" not in page
    assert "band-caret" in page
    assert "proc-dot" not in page
    # Narrative is present via its stable hook.
    assert "data-proc-narrative" in page
    # Attributes the serializer reads are still present.
    for attr in ("data-proc-code", "data-proc-name", "data-proc-assert",
                 "data-proc-pct", "data-proc-count", "data-proc-narrative"):
        assert attr in page
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/plane/test_logic_bands.py::test_builder_get_renders_procedure_title_layout -q`
Expected: FAIL (`"Tolerance"`/`band-caret` absent; `"Fail if"`/`proc-dot` still present).

- [ ] **Step 3: Add the Inputs-band caret + restructure the procedure summary in the template**

In `controlflow_sdk/plane/templates/partials/_pipe_cards.html`:

(a) At the top of the file (after the opening comment block, before the `insert_zone` macro), define the reworded, apostrophe-free tooltip once:

```jinja
{% set ASSERTION_HELP = "The audit assertion this procedure verifies — the specific claim it proves about the control (for example: Segregation of duties, Authorization, Completeness, Existence). Shown as the procedure subtitle in the workpaper." %}
```

(b) Inputs band summary — add a caret as the first child (replace the existing `<summary class="band-head">` opening through its `band-sub` span, lines 42–45):

```jinja
  <summary class="band-head">
    <span class="band-caret" aria-hidden="true">▸</span>
    <span class="band-title">Inputs &amp; shared steps</span>
    <span class="band-sub muted">data sources and steps feeding more than one procedure</span>
  </summary>
```

(c) Procedure section — replace the `<details …>` opening and the entire `<summary> … </summary>` (lines 58–89) with:

```jinja
<details class="proc-section" data-proc-section data-band-key="{{ band.key }}"
         style="border-left-color:{{ band.proc.color }}" open>
  <summary class="band-head proc-head-row">
    <span class="band-caret" aria-hidden="true">▸</span>
    <span class="proc-head" data-proc-head data-proc-id="{{ band.proc.id }}">
      <div class="proc-identity">
        <input class="proc-in proc-code-chip" data-proc-code value="{{ band.proc.code }}" placeholder="P1" aria-label="Code">
        <input class="proc-in proc-name-title" type="text" data-proc-name value="{{ band.proc.name }}" placeholder="Procedure name" aria-label="Procedure name">
        <button type="button" class="proc-name-pencil" data-proc-name-edit aria-label="Edit procedure name"></button>
        <span class="proc-identity-spacer"></span>
        <button type="button" class="proc-del-btn" data-proc-del aria-label="Remove procedure">✕</button>
      </div>
      <div class="proc-settings">
        <div class="proc-field proc-field-assert">
          <label class="proc-field-label">Assertion
            <span class="proc-help" tabindex="0" role="img" aria-label="What is an assertion?"
                  title="{{ ASSERTION_HELP }}">&#9432;</span>
          </label>
          <input class="proc-in proc-assert-in" data-proc-assert value="{{ band.proc.assertion }}" placeholder="e.g. Segregation of duties" aria-label="Assertion">
        </div>
        <div class="proc-field proc-field-tol">
          <label class="proc-field-label">Tolerance</label>
          <div class="proc-tol-row">
            <span class="proc-tol-op">&#8804;</span>
            <input class="proc-in proc-thr-in" data-proc-pct value="{{ band.proc.failure_threshold_pct if band.proc.failure_threshold_pct is not none else '' }}" placeholder="&#8212;" aria-label="Threshold percent">
            <span class="proc-tol-op">% or</span>
            <input class="proc-in proc-thr-in" data-proc-count value="{{ band.proc.failure_threshold_count if band.proc.failure_threshold_count is not none else '' }}" placeholder="&#8212;" aria-label="Threshold count">
            <span class="proc-tol-op">items</span>
            <span class="proc-tol-hint">· blank = zero</span>
          </div>
        </div>
      </div>
      <div class="proc-field proc-field-narr">
        <label class="proc-field-label">Narrative</label>
        <textarea class="proc-in proc-narr" data-proc-narrative rows="2"
                  placeholder="What this procedure tests and why"
                  aria-label="Procedure narrative">{{ band.proc.narrative }}</textarea>
      </div>
    </span>
  </summary>
```

(Leave the `<div class="band-body">…</div>` and the rest of the loop unchanged.)

- [ ] **Step 4: Rework the CSS block**

In `controlflow_sdk/plane/static/app.css`, replace the block from `.band-inputs, .proc-section {` (line 706) through `.proc-head .proc-thr-in { width: 56px; }` (line 791) — i.e. everything up to but **not** including `#proc-add` (792) — with:

```css
.band-inputs, .proc-section {
  border: 1px solid var(--border-default); border-radius: var(--radius-card);
  background: var(--bg-surface-2); margin-bottom: 14px; padding: 0 14px 8px;
}
/* Per-procedure accent: a left stripe (color set inline from procedure_color()). */
.proc-section { border-left: 3px solid var(--border-strong); }

.band-head {
  /* Top-align so the caret sits on the identity row, not centered on the tall column. */
  display: flex; flex-wrap: wrap; align-items: flex-start; gap: 8px;
  padding: 12px 0 10px; cursor: pointer; list-style: none;
}
.band-head::-webkit-details-marker { display: none; }
/* Caret is a real element (robust click target + e2e hook), replacing the old ::before. */
.band-caret { flex: 0 0 auto; color: var(--text-tertiary); font-size: 12px; transition: transform .12s; }
.proc-head-row .band-caret { margin-top: 6px; }
details[open] > .band-head > .band-caret { transform: rotate(90deg); }
.band-title { font-weight: 600; font-size: 14px; }
.band-sub { font-size: 12px; }
.band-body { padding-bottom: 6px; }
.proc-empty { font-size: 12px; margin: 8px 0; }

/* The procedure editor column (three stacked tiers). */
.proc-head { display: flex; flex-direction: column; align-items: stretch; gap: 12px; flex: 1; min-width: 0; }
.proc-head .proc-in {
  font-size: 13px; padding: 5px 8px; margin: 0; width: auto;
  background: var(--bg-input); color: var(--text-primary);
  border: 1px solid var(--border-default); border-radius: var(--radius-input);
}
.proc-head .proc-in:focus {
  outline: none; border-color: var(--accent-primary); box-shadow: 0 0 0 3px var(--accent-muted);
}

/* Tier 1 — identity bar. */
.proc-identity { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.proc-identity-spacer { flex: 1; }
/* Code chip: an editable input styled as a procedure-color-tinted chip. */
.proc-head .proc-code-chip {
  width: 46px; text-align: center; font-weight: 600; font-size: 11px;
  font-family: var(--font-mono); letter-spacing: .02em; padding: 3px 6px;
  border-radius: 5px;
}
/* Name as an in-place heading. The `input.` qualifier raises specificity to (0,2,1)
   so it beats the later global `input[type="text"]` block (0,1,1) — learning 0032. */
.proc-head input.proc-name-title {
  flex: 1; min-width: 200px; margin: 0;
  font-family: var(--font-sans); font-size: 19px; font-weight: 650; letter-spacing: -.01em;
  color: var(--text-primary); background: transparent;
  border: 1px solid transparent; border-radius: var(--radius-input); padding: 4px 8px;
}
.proc-head input.proc-name-title:hover { border-color: var(--border-default); }
.proc-head input.proc-name-title:focus {
  outline: none; background: var(--bg-input);
  border-color: var(--accent-primary); box-shadow: 0 0 0 3px var(--accent-muted);
}
/* Quiet icon buttons (pencil + delete); border/colour wake on card hover. */
.proc-name-pencil, .proc-del-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px; padding: 0; flex: 0 0 auto;
  border: 1px solid transparent; border-radius: 999px;
  background: transparent; color: var(--text-tertiary); cursor: pointer;
  font-size: 13px; line-height: 1; transition: color .15s, border-color .15s;
}
.proc-section:hover .proc-name-pencil, .proc-section:hover .proc-del-btn {
  border-color: var(--border-strong); color: var(--text-secondary);
}
.proc-name-pencil:hover { color: var(--accent-primary); border-color: var(--accent-primary); }
.proc-del-btn:hover { color: var(--status-critical); border-color: var(--status-critical); }
.proc-name-pencil::before { content: '✎'; font-size: 12px; }

/* Tier 2 — settings strip; top-aligned so the three field labels share one baseline. */
.proc-settings { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 22px; }
.proc-field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.proc-field-assert { flex: 1 1 320px; }
.proc-field-tol { flex: 0 0 auto; }
.proc-field-label {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 10.5px; font-weight: 600; letter-spacing: .07em; text-transform: uppercase;
  color: var(--text-tertiary);
}
.proc-help {
  display: inline-flex; align-items: center; justify-content: center;
  width: 13px; height: 13px; font-size: 10px; cursor: help;
  color: var(--text-tertiary); border: 1px solid var(--border-strong); border-radius: 999px;
}
.proc-help:hover, .proc-help:focus { color: var(--accent-primary); border-color: var(--accent-primary); outline: none; }
.proc-head .proc-assert-in { width: 100%; }
.proc-tol-row { display: flex; align-items: center; gap: 6px; }
.proc-tol-op, .proc-tol-hint { font-size: 11.5px; color: var(--text-tertiary); }
.proc-head .proc-thr-in { width: 48px; text-align: center; }

/* Tier 3 — narrative (full-width row in the column). */
.proc-head .proc-narr {
  width: 100%; resize: vertical; min-height: 38px; font-family: inherit; line-height: 1.4;
}
```

(Leave `#proc-add`, `.proc-chip`, `.pipe-chips`, and the `@media (max-width: 640px)` block untouched.)

- [ ] **Step 5: Fix the e2e collapse interaction (it clicked the removed `.proc-dot`)**

In `tests/e2e/test_smoke.py`, update the collapse step. Replace the comment at lines 315–317 and the click at line 319, and the re-expand click at line 336:

- Line 315–317 comment → 
  ```python
      # ── Step 4b: collapse by clicking the caret (inside <summary>, outside .proc-head) ─
      # Clicking .band-caret IS inside <summary> but NOT inside .proc-head, so the
      # JS handler does not call e.preventDefault() and the <details> toggles.
  ```
- Line 319 `section.locator(".proc-dot").click()` → `section.locator(".band-caret").click()`
- Line 336 `section.locator(".proc-dot").click()` → `section.locator(".band-caret").click()`

- [ ] **Step 6: Run the plane suite + the smoke e2e**

Run: `python -m pytest tests/plane -q`
Expected: PASS (incl. the updated `test_builder_get_renders_procedure_title_layout`).

Run: `python -m pytest tests/e2e/test_smoke.py -m browser -q`
Expected: PASS (collapse now toggles via `.band-caret`).

- [ ] **Step 7: Lint/type gates**

Run: `python -m ruff check . && python -m mypy controlflow_sdk`
Expected: clean.

- [ ] **Step 8: Commit + push**

```bash
git add controlflow_sdk/plane/templates/partials/_pipe_cards.html \
        controlflow_sdk/plane/static/app.css \
        tests/plane/test_logic_bands.py tests/e2e/test_smoke.py
git commit -m "Procedure header: 3-tier card (server template + CSS), caret toggle replaces dot"
git push -u origin HEAD
```
(Include the standard co-author/session trailer in the commit message.)

---

## Task 2: Mirror the redesign in the JS "＋ Add procedure" builder

**Files:**
- Modify: `controlflow_sdk/plane/templates/logic_builder.html` (`newProcedureSection()`, ~337–382)
- Test: `tests/e2e/test_multi_procedure.py` (add a structural-parity + zero-`pageerror` test)

**Interfaces:**
- Consumes (from Task 1): the exact structure/classes emitted by the server template, which this builder must reproduce verbatim, plus the apostrophe-free `ASSERTION_HELP` copy.
- Produces: a JS-added `<details class="proc-section">` whose `<summary>` is structurally identical to a server-rendered one (same classes, same `data-proc-*` hooks, same `band-caret`).

- [ ] **Step 1: Write the failing e2e (add-procedure parity + no script error)**

Append to `tests/e2e/test_multi_procedure.py`:

```python
def test_add_procedure_button_builds_the_new_card_shape(page, live_server):
    """Clicking ＋ Add procedure builds a section structurally identical to a
    server-rendered one, with no inline-script pageerror (learning 0040)."""
    import re
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    # Seed a control with one procedure, open the builder.
    _seed_two_procedure_control(page, live_server)  # see Step 3 note if a helper is needed
    page.goto(f"{live_server}/controls/coltest/logic/builder")
    page.wait_for_load_state("load")

    before = page.locator("details[data-proc-section]").count()
    page.get_by_role("button", name=re.compile("Add procedure")).click()
    new = page.locator("details[data-proc-section]").last

    # Structural parity with the server-rendered card.
    expect(new.locator(".band-caret")).to_have_count(1)
    expect(new.locator("[data-proc-head]")).to_have_count(1)
    for sel in ("[data-proc-code]", "[data-proc-name]", "[data-proc-assert]",
                "[data-proc-pct]", "[data-proc-count]", "[data-proc-narrative]",
                "[data-proc-name-edit]", "[data-proc-del]"):
        expect(new.locator(sel)).to_have_count(1)
    expect(new.get_by_text("Tolerance", exact=True)).to_be_visible()
    assert page.locator("details[data-proc-section]").count() == before + 1
    assert errors == []
```

> Note: reuse whatever seed/fixture pattern the existing tests in this file use (e.g. the `page`/`live_server` fixtures already imported there). If no single-control seed helper exists, inline the same setup the file's other tests use to reach `/controls/coltest/logic/builder`. Do not introduce `localStorage.setItem` to fake state (learning 0037).

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/e2e/test_multi_procedure.py::test_add_procedure_button_builds_the_new_card_shape -m browser -q`
Expected: FAIL (the builder still emits the old `.proc-dot`/`proc-title-row` shape; no `.band-caret`, no "Tolerance").

- [ ] **Step 3: Rewrite `newProcedureSection()` to the new shape**

In `controlflow_sdk/plane/templates/logic_builder.html`, replace the `sec.innerHTML = …` assignment in `newProcedureSection()` (the string spanning ~343–379) with the new structure. Keep the trailing `sec.querySelector('[data-proc-code]').value = code;` line:

```javascript
      sec.innerHTML =
        '<summary class="band-head proc-head-row">' +
        '<span class="band-caret" aria-hidden="true">▸</span>' +
        '<span class="proc-head" data-proc-head data-proc-id="' + pid + '">' +
        '<div class="proc-identity">' +
        '<input class="proc-in proc-code-chip" data-proc-code placeholder="P1" aria-label="Code">' +
        '<input class="proc-in proc-name-title" type="text" data-proc-name placeholder="Procedure name" aria-label="Procedure name">' +
        '<button type="button" class="proc-name-pencil" data-proc-name-edit aria-label="Edit procedure name"></button>' +
        '<span class="proc-identity-spacer"></span>' +
        '<button type="button" class="proc-del-btn" data-proc-del aria-label="Remove procedure">✕</button>' +
        '</div>' +
        '<div class="proc-settings">' +
        '<div class="proc-field proc-field-assert">' +
        '<label class="proc-field-label">Assertion ' +
        '<span class="proc-help" tabindex="0" role="img" aria-label="What is an assertion?" ' +
        'title="The audit assertion this procedure verifies — the specific claim it proves about the control (for example: Segregation of duties, Authorization, Completeness, Existence). Shown as the procedure subtitle in the workpaper.">ⓘ</span>' +
        '</label>' +
        '<input class="proc-in proc-assert-in" data-proc-assert placeholder="e.g. Segregation of duties" aria-label="Assertion">' +
        '</div>' +
        '<div class="proc-field proc-field-tol">' +
        '<label class="proc-field-label">Tolerance</label>' +
        '<div class="proc-tol-row">' +
        '<span class="proc-tol-op">≤</span>' +
        '<input class="proc-in proc-thr-in" data-proc-pct placeholder="—" aria-label="Threshold percent">' +
        '<span class="proc-tol-op">% or</span>' +
        '<input class="proc-in proc-thr-in" data-proc-count placeholder="—" aria-label="Threshold count">' +
        '<span class="proc-tol-op">items</span>' +
        '<span class="proc-tol-hint">· blank = zero</span>' +
        '</div></div></div>' +
        '<div class="proc-field proc-field-narr">' +
        '<label class="proc-field-label">Narrative</label>' +
        '<textarea class="proc-in proc-narr" data-proc-narrative rows="2" ' +
        'placeholder="What this procedure tests and why" ' +
        'aria-label="Procedure narrative"></textarea>' +
        '</div>' +
        '</span></summary>' +
        '<div class="band-body"><p class="muted proc-empty">No test yet — insert a ' +
        '<strong>Test</strong> below to give this procedure a result.</p>' +
        '<div class="pipe-insert pipe-insert-empty">' +
        '<button type="button" class="pipe-insert-toggle" data-insert-toggle aria-label="Insert a step here">+</button>' +
        '<div class="pipe-insert-menu"><span class="pipe-insert-hint">Insert step</span>' +
        '<button type="button" class="btn btn-sm btn-add" data-insert data-type="test" data-up="" data-down="" data-proc="' + pid + '">Test</button>' +
        '</div></div></div>';
```

Note the tooltip text now contains **no apostrophes**, so it is safe inside the single-quoted JS string with a double-quoted `title="…"` attribute (learning 0040).

- [ ] **Step 4: Run the new e2e + the existing multi-procedure e2e**

Run: `python -m pytest tests/e2e/test_multi_procedure.py -m browser -q`
Expected: PASS (new parity test + the existing add/threshold tests).

- [ ] **Step 5: Commit + push**

```bash
git add controlflow_sdk/plane/templates/logic_builder.html tests/e2e/test_multi_procedure.py
git commit -m "Procedure header: mirror 3-tier shape in the Add-procedure JS builder"
git push -u origin HEAD
```
(Include the standard co-author/session trailer.)

---

## Task 3: Whole-tree sweep, teeth-checks, and full gates

**Files:**
- Modify: `tests/e2e/test_smoke.py` (add a computed-style teeth-check)
- Possibly modify: any other `tests/` file still referencing a removed literal (sweep)

**Interfaces:**
- Consumes: the finished structure from Tasks 1–2.
- Produces: green full suite + e2e + ruff + mypy; pinned visual intent (name size + stripe).

- [ ] **Step 1: Sweep the whole tests/ tree for stale literals**

Run: `grep -rn "proc-dot\|proc-title-row\|proc-fields-row\|proc-narrative-row\|proc-threshold\|proc-code-badge\|Fail if\|% or" tests/`
Expected after Tasks 1–2: only intentional new references (e.g. `% or` inside the new tolerance assertions) remain. Migrate any straggler that pins removed structure to its new equivalent (`band-caret`, `Tolerance`, `data-proc-narrative`, `proc-field-narr`). If none remain, proceed.

- [ ] **Step 2: Add a computed-style teeth-check (cascade tie is invisible in source — 0032 corollary)**

In `tests/e2e/test_smoke.py`, immediately after the section-open assertions (after line 313, `expect(section).to_have_attribute("open", "")`), add:

```python
    # Teeth-check the styled-field intent — the name renders as a 19px heading and
    # the card carries the 3px accent stripe (both invisible in source/diff; 0032).
    expect(section.locator("input.proc-name-title").first).to_have_css("font-size", "19px")
    expect(section).to_have_css("border-left-width", "3px")
```

- [ ] **Step 3: Run that test**

Run: `python -m pytest tests/e2e/test_smoke.py -m browser -q`
Expected: PASS.

- [ ] **Step 4: Full suite + all gates**

Run: `python -m pytest -q`
Expected: PASS, no warnings.

Run: `python -m pytest tests/e2e -m browser -q`
Expected: PASS.

Run: `python -m ruff check . && python -m mypy controlflow_sdk`
Expected: clean.

- [ ] **Step 5: Commit + push (only if Step 1 or 2 changed files)**

```bash
git add tests/
git commit -m "Procedure header: teeth-check name size + accent stripe; tests/ sweep"
git push -u origin HEAD
```
(Include the standard co-author/session trailer. Skip the commit if no files changed.)

---

## Self-Review

**Spec coverage:**
- 3-tier layout (identity / settings / narrative) → Task 1 Step 3–4. ✓
- Color dot → left stripe; code → chip; quiet hover icons → Task 1 (markup + CSS). ✓
- Peer "Tolerance" label + always-visible inputs + "blank = zero" hint, labels aligned → Task 1 Step 3–4. ✓
- Both render sites kept consistent → Task 1 (template) + Task 2 (JS builder). ✓
- Apostrophe-free tooltip in both sites (0040) → Task 1 Step 3(a) + Task 2 Step 3. ✓
- Preserve all `data-proc-*` hooks → asserted in Task 1 Step 1 and Task 2 Step 1. ✓
- Name specificity (0032) preserved + teeth-checked → Task 1 Step 4 + Task 3 Step 2. ✓
- Tokens + both themes (0005) → Task 1 Step 4 (orchestrator verifies light/dark live before finishing). ✓
- e2e re-run + migrated (0012/0031) → Task 1 Step 5–6, Task 3 Step 1–4. ✓
- No bundle/contract change (0015) → no producer/schema files touched. ✓

**Deviation from spec (enumerated per learning 0039):** the spec said "no change to the Inputs band." Task 1 makes ONE minor, deliberate change there — replacing the `::before` pseudo-caret with a real `<span class="band-caret">` element so collapse has a robust click target for users and e2e (the old test toggled via the now-removed `.proc-dot`). No restructure of the band's content. This is the only spec deviation; no spec-mandated assertion is dropped.

**Placeholder scan:** every code/test step shows full content; the one note (Task 2 Step 1) points the implementer at the file's existing fixtures rather than inventing a signature. No TBD/TODO.

**Type/name consistency:** classes introduced in Task 1 (`band-caret`, `proc-identity`, `proc-code-chip`, `proc-settings`, `proc-field*`, `proc-tol-*`) are the exact strings asserted in Tasks 2–3 and emitted by both render sites. `data-proc-*` hook names match `serializeProcedures()`.
