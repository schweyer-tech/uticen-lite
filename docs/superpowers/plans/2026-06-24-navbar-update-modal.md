# Navbar Update Modal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the navbar update indicator show status text on hover and open a modal on click so users can update without fighting a disappearing popover.

**Architecture:** Keep the existing update check routes and background polling logic. Move the interactive affordance into the header indicator partial and add a small modal shell in the base layout, with lightweight JS handling open/close and HTMX continuing to own the update request itself.

**Tech Stack:** FastAPI, Jinja2 templates, HTMX, plain browser JS, pytest.

---

### Task 1: Replacing the hover popover with a tooltip-driven modal trigger

**Files:**
- Modify: `uticen_lite/plane/templates/partials/header_update_indicator.html`
- Modify: `uticen_lite/plane/templates/base.html:51-78`
- Modify: `uticen_lite/plane/static/app.css:555-620`

- [ ] **Step 1: Write the failing test**

```python
def test_header_indicator_renders_tooltip_and_modal_trigger(client, monkeypatch):
    conn = connect(client.app.state.project_root)
    repo.set_check_updates_on_launch(conn, True)
    conn.close()
    monkeypatch.setattr(
        "uticen_lite.plane.routes.updates.detect_install",
        lambda: InstallMethod.PIP,
    )
    monkeypatch.setattr(
        "uticen_lite.plane.routes.updates.check_for_update",
        lambda method: UpdateInfo(method, "0.1.0", "0.2.0", True, "Version 0.2.0 is available."),
    )
    resp = client.get("/updates/indicator")
    assert resp.status_code == 200
    assert 'title="Update available: 0.2.0"' in resp.text
    assert 'data-update-indicator-open' in resp.text
    assert 'update-popover' not in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/plane/test_settings_updates.py -k header_indicator_renders_tooltip_and_modal_trigger -v`

Expected: FAIL because the current markup still renders the hover popover/actions instead of the new modal trigger.

- [ ] **Step 3: Write minimal implementation**

```html
<!-- header_update_indicator.html -->
<button type="button"
        class="update-indicator update-available"
        aria-label="Update available: {{ info.latest }}"
        title="Update available: {{ info.latest }}"
        data-update-indicator-open>
  <span class="indicator-dot"></span>
  <span class="sr-only">Update available: {{ info.latest }}</span>
</button>
```

```html
<!-- base.html -->
<dialog id="update-modal" class="update-modal" aria-label="Update available">
  <form method="dialog" class="update-modal-shell">
    <h2>Update available</h2>
    <p id="update-modal-text"></p>
    <div class="update-modal-actions">
      <button class="btn btn-primary" hx-post="/upgrade" hx-target="body" hx-swap="innerHTML">
        Update now
      </button>
      <button class="btn btn-ghost" data-update-modal-close>Close</button>
    </div>
  </form>
</dialog>
<script>
  function openUpdateModal(text) {
    var dialog = document.getElementById("update-modal");
    var modalText = document.getElementById("update-modal-text");
    if (!dialog || !modalText) return;
    modalText.textContent = text;
    if (dialog.showModal) dialog.showModal();
  }
  document.addEventListener("click", function (event) {
    var opener = event.target.closest("[data-update-indicator-open]");
    if (opener) {
      openUpdateModal(opener.getAttribute("aria-label") || "");
    }
    if (event.target.closest("[data-update-modal-close]")) {
      var dialog = document.getElementById("update-modal");
      if (dialog && dialog.close) dialog.close();
    }
  });
</script>
```

```css
/* app.css */
.update-indicator[title] { cursor: help; }
.update-modal::backdrop { background: rgba(0, 0, 0, 0.45); }
.update-modal {
  border: 1px solid var(--border-default);
  border-radius: var(--radius-card);
  padding: 0;
  background: var(--bg-surface-1);
  color: var(--text-primary);
  max-width: 420px;
  width: calc(100vw - 32px);
}
.update-modal-shell { padding: 20px; display: grid; gap: 12px; }
.update-modal-actions { display: flex; gap: 8px; justify-content: flex-end; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/plane/test_settings_updates.py -k header_indicator_renders_tooltip_and_modal_trigger -v`

Expected: PASS with the modal trigger and tooltip attributes present, and no hover popover markup.

- [ ] **Step 5: Commit**

```bash
git add uticen_lite/plane/templates/partials/header_update_indicator.html uticen_lite/plane/templates/base.html uticen_lite/plane/static/app.css
git commit -m "Make header update indicator open a modal" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 2: Verifying modal interaction in a browser smoke test

**Files:**
- Create: `tests/e2e/test_update_indicator_modal.py`

- [ ] **Step 1: Write the failing test**

```python
from playwright.sync_api import expect

def test_header_indicator_opens_modal(page, live_server):
    base = live_server
    page.route("**/updates/indicator", lambda route: route.fulfill(
        status=200,
        content_type="text/html",
        body="""
        <div id=\"header-update-indicator\" class=\"update-indicator-wrap\">
          <button type=\"button\"
                  class=\"update-indicator update-available\"
                  title=\"Update available: 0.2.0\"
                  aria-label=\"Update available: 0.2.0\"
                  data-update-indicator-open>
            <span class=\"indicator-dot\"></span>
          </button>
        </div>
        """,
    ))
    page.goto(base + "/")

    indicator = page.locator("[data-update-indicator-open]")
    expect(indicator).to_be_visible()
    indicator.click()

    modal = page.locator("#update-modal")
    expect(modal).to_be_visible()
    expect(modal.get_by_role("button", name="Update now")).to_be_visible()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/e2e/test_update_indicator_modal.py -m browser -v`

Expected: FAIL until the modal shell and click handler are in place.

- [ ] **Step 3: Write minimal implementation**

```python
# The implementation is the browser test above, which locks in the click-to-modal
# flow against the live app while keeping the update indicator network response
# deterministic via route interception.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/e2e/test_update_indicator_modal.py -m browser -v`

Expected: PASS; the indicator click opens the modal and exposes the update action.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_update_indicator_modal.py
git commit -m "Add browser coverage for header update modal" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```
