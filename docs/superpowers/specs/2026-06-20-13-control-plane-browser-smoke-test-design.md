# Spec: Browser smoke test for the control plane in CI (issue #13)
**Issue:** #13 · **Date:** 2026-06-20 · **Status:** approved-design

## Problem (1–3 sentences)
The control plane's web surface is covered only by FastAPI `TestClient` route tests, so nothing exercises the rendered UI end-to-end in CI — the multi-run workpaper-ordering bug in PR #5 was caught by human review, not a test. We need a Playwright smoke test that drives a live `controlplane` through the real authoring flow (upload + map → author rule control → run → assert run view → export → validate bundle), kept out of the fast unit lane and gated to block only on PRs that touch `plane/`.

## Locked decisions
- Playwright end-to-end smoke test, **REQUIRED (blocking)** on PRs that touch `plane/` (or the contract/templates it depends on); a separate CI job that installs the browser, gated by a paths-filter, out of the fast unit lane.
- Use `pytest-playwright` (sync API) under a marker `@pytest.mark.browser`, in a dedicated `tests/e2e/` dir excluded from the default `pytest -q` run via pyproject `addopts`/`markers` so the fast suite stays pristine and offline.
- The flow: launch `controlplane` (uvicorn) on an ephemeral port + temp-dir db → browser uploads a CSV and maps columns → author a rule control → run → assert run-view totals + violations → POST `/export` → assert the downloaded bundle validates against `contract/bundle.schema.json`.
- Add the test deps to a dev/test extra; add the browser-install step to the CI job only (not the fast lane).
- Honor cardinal rule 0001 (the export assertion IS the contract guard) and learning 0002 (per-handler connections; this test only drives the live app, it doesn't change handler code).

## Design

### Architecture overview
A new `e2e` extra carries `pytest-playwright`. A new `tests/e2e/` package holds the browser test, marked `@pytest.mark.browser`. The fast lane (`pytest -q`) is configured to ignore `tests/e2e/` and to error on the unregistered marker, so the default suite stays offline and pristine. A second CI job (`e2e`) installs the `e2e` extra + Chromium, launches a real `controlplane` server against a temp engagement, runs the marked test, and blocks the PR — but only when a `dorny/paths-filter` step reports that `plane/`, `contract/`, or the bundle code changed.

The server is launched **in-process via a background thread running `uvicorn.Server`**, not a subprocess. Rationale: `create_app(project_root)` already builds the full app from a `Path`, the existing tests construct it directly, and a threaded server lets the fixture pick an ephemeral port, pass the temp dir, and tear down deterministically without parsing subprocess stdout. The handlers open their own sqlite connections per request (learning 0002), so a threaded server is safe — no shared connection crosses threads. We bind to `127.0.0.1` on an OS-assigned free port.

### Files to create

**1. `tests/e2e/__init__.py`** — empty package marker (mirrors `tests/plane/__init__.py`).

**2. `tests/e2e/conftest.py`** — fixtures: a temp engagement, a threaded live server, and a Playwright `page` pointed at it.

```python
from __future__ import annotations
import socket, threading, time
from pathlib import Path
from collections.abc import Iterator

import pytest
import uvicorn

from controlflow_sdk.plane.app import create_app
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.migrations import migrate


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def engagement(tmp_path: Path) -> Path:
    # Mirror tests/plane/conftest.py so the dashboard skips the setup screen.
    (tmp_path / "data").mkdir(exist_ok=True)
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_project(conn, name="E2E Co")
    conn.close()
    return tmp_path


@pytest.fixture
def live_server(engagement: Path) -> Iterator[str]:
    port = _free_port()
    app = create_app(engagement)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Wait for startup (uvicorn flips .started once the socket is listening).
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("controlplane did not start in time")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
```

> Note: `engagement` pre-names the project ("E2E Co") so the dashboard renders the real Controls page, not the `setup.html` onboarding screen (see `dashboard.py`: it shows `setup.html` only when the project has no name). This keeps the flow deterministic. Loading the demo is an alternative path (locked decision allows "load demo OR upload"), but we choose **upload + map** because it exercises the column-mapping surface that route tests under-cover and keeps the CSV tiny and deterministic.

**3. `tests/e2e/test_smoke.py`** — the single marked browser test. Uses `pytest-playwright`'s `page` fixture (sync API). Drives the exact DOM confirmed in the templates:

```python
import io, json, zipfile
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from controlflow_sdk.schema.validate import validate_bundle

CSV = b"user_id,can_create,can_approve\nU1,true,true\nU2,true,false\n"


@pytest.mark.browser
def test_author_run_export_smoke(page: Page, live_server: str, tmp_path: Path) -> None:
    base = live_server

    # 1. Dashboard renders (named engagement → Controls page, not setup).
    page.goto(base + "/")
    expect(page.get_by_role("heading")).to_contain_text("E2E Co")  # adjust to dashboard.html h1

    # 2. Upload a CSV source.  GET /sources/new has the multipart form
    #    (source_new.html): #s-id, file input[name=file], #s-asof.
    page.goto(base + "/sources/new")
    page.fill("#s-id", "users")
    page.set_input_files("input[name='file']",
                         files=[{"name": "users.csv", "mimeType": "text/csv", "buffer": CSV}])
    page.fill("#s-asof", "2026-01-31")
    page.click("button[type=submit]")
    # Redirects (303) to /sources/users (the Definition tab — source_edit.html).
    expect(page).to_have_url(base + "/sources/users")

    # 3. Author a rule control. GET /controls/new (control_edit.html) has the
    #    rule builder (rule_builder.html) + conditions (rule_condition.html).
    page.goto(base + "/controls/new")
    page.fill("#f-id", "sod")
    page.fill("#f-title", "Segregation of duties")
    # rule fields
    page.fill("input[name='rule_description']", "User {user_id} can both create and approve")
    page.fill("input[name='rule_item_key']", "user_id")
    page.select_option("select[name='rule_severity']", "high")
    # one condition: can_create eq true  (first condition row is pre-rendered)
    page.fill("input[name='cond_column']", "can_create")
    page.select_option("select[name='cond_op']", "eq")
    page.fill("input[name='cond_value']", "true")
    # bind the source (checkbox value=users)
    page.check("input[name='source_ids'][value='users']")
    # fail on any exception
    page.fill("#f-cnt", "0")
    page.click("button[type=submit]")  # POST /controls -> 303 /controls/sod
    expect(page).to_have_url(base + "/controls/sod")

    # 4. Run it.  control_edit.html has no Run button → drive POST via the run
    #    endpoint by visiting the dashboard Run action, OR submit the run form.
    #    (See "Run trigger" below for the exact element.)
    page.goto(base + "/")
    page.click("text=Run")  # dashboard run button for control sod
    # run handler 303-redirects to /controls/sod/runs/<run_id> (run_view.html)
    expect(page).to_have_url(lambda u: "/runs/" in u)

    # 5. Assert run-view totals + violations (run_view.html tiles + table).
    expect(page.locator(".tile-value").first).to_have_text("2")   # Records tested
    expect(page.get_by_text("Operated with deficiencies")).to_be_visible()
    expect(page.get_by_role("cell", name="U1")).to_be_visible()   # the one violation
    expect(page.get_by_text("U2")).to_have_count(0)               # U2 passes

    # 6. Export the bundle and validate it against the contract.
    page.goto(base + "/export")
    with page.expect_download() as dl_info:
        page.click("button[type=submit]")          # POST /export → FileResponse bundle.zip
    download = dl_info.value
    out = tmp_path / "bundle.zip"
    download.save_as(out)

    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["schema_version"] == "1.0"
    assert validate_bundle(manifest) == []            # THE contract guard (cardinal rule 0001)
    assert any(c["id"] == "sod" for c in manifest["controls"])
```

> **Selector grounding & assertions** — selectors above are taken verbatim from the templates I read: `source_new.html` (`#s-id`, `input[name=file]`, `#s-asof`), `control_edit.html` (`#f-id`, `#f-title`, `#f-cnt`, `input[name=source_ids]`), `rule_builder.html` / `rule_condition.html` (`select[name=rule_severity]`, `input[name=cond_column]`, `select[name=cond_op]`, `input[name=cond_value]`, `input[name=rule_description]`, `input[name=rule_item_key]`), and `run_view.html` (`.tile-value`, the `Operated with deficiencies` pill, the exceptions `<table>` rows). The implementing agent MUST take a Playwright snapshot of the actual `/` dashboard page once to confirm the dashboard h1 text and the **Run trigger** element, then pin the exact selectors (see Risks).

**Run trigger (must verify against `dashboard.html`):** the run is `POST /controls/{id}/run` (see `runs.py`). `control_edit.html` has no run button, so the test triggers the run from the dashboard. The implementing agent must open `dashboard.html` and confirm the run control surface (likely a `<form method="post" action="/controls/sod/run">` with a "Run" submit button per row). Pin the click to that element (e.g. a row-scoped `form[action$='/controls/sod/run'] button`). If the dashboard has no run button, add the run via `page.goto`-driven form submission is not possible (POST), so instead drive it by clicking the row's run form; fall back to `page.request.post(base + "/controls/sod/run")` followed by reloading the redirected URL **only** if no UI run button exists (document the choice in a comment). Prefer the real UI button to honor "guard the rendered UI."

### Files to modify

**4. `pyproject.toml`**
- Add an `e2e` optional-dependency extra:
  ```toml
  e2e = ["pytest-playwright>=0.5"]
  ```
  (`pytest-playwright` pulls in `playwright`; the Chromium binary is installed separately by `playwright install` in CI — not a pip dep.)
- Extend `[tool.pytest.ini_options]` (currently only `filterwarnings`) to register the marker and keep e2e out of the fast lane:
  ```toml
  [tool.pytest.ini_options]
  addopts = "--ignore=tests/e2e"
  markers = [
    "browser: end-to-end Playwright browser test (opt-in; excluded from the fast unit lane)",
  ]
  filterwarnings = [
    "ignore:Using .httpx. with .starlette.testclient.:starlette.exceptions.StarletteDeprecationWarning",
  ]
  ```
  `--ignore=tests/e2e` keeps `pytest -q` from even collecting the browser test (so the fast lane never needs `pytest-playwright` installed and stays offline/pristine). The CI `e2e` job overrides this by targeting the dir explicitly: `pytest tests/e2e -m browser`.

**5. `.github/workflows/ci.yml`** — add a paths-filter and a gated `e2e` job alongside the existing `test` matrix job (do not touch the fast lane's steps).
- Add a `changes` job that runs `dorny/paths-filter@v3` to detect plane-relevant changes:
  ```yaml
  changes:
    runs-on: ubuntu-latest
    outputs:
      plane: ${{ steps.filter.outputs.plane }}
    steps:
      - uses: actions/checkout@v7
      - uses: dorny/paths-filter@v3
        id: filter
        with:
          filters: |
            plane:
              - 'controlflow_sdk/plane/**'
              - 'contract/**'
              - 'controlflow_sdk/bundle/**'
              - 'controlflow_sdk/schema/**'
              - 'tests/e2e/**'
              - 'pyproject.toml'
              - '.github/workflows/ci.yml'
  ```
- Add the `e2e` job, gated on the filter, single Python (3.12), installing the browser only here:
  ```yaml
  e2e:
    needs: changes
    if: needs.changes.outputs.plane == 'true'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
          cache: "pip"
          cache-dependency-path: "pyproject.toml"
      - name: Install dependencies
        run: pip install -e ".[plane,e2e]"
      - name: Install Playwright browser
        run: python -m playwright install --with-deps chromium
      - name: Run browser smoke test
        run: pytest tests/e2e -m browser
  ```
- **Blocking semantics:** the `e2e` job is REQUIRED. Because `if:` makes it skip when unaffected, do NOT mark it required via a branch-protection rule that fails on "skipped" (a skipped required check blocks merges). Instead the locked "blocking on PRs that touch plane/" intent is satisfied by the job running-and-failing only when relevant; document in the PR that branch protection should treat `e2e` as a required check that GitHub reports green/neutral when skipped. (GitHub treats a `needs`+`if` skipped job as "skipped", which does not block required-status-check gating when the job didn't run.) If the repo's branch protection cannot express "required-if-run", use the standard alternative: keep the job's name stable (`e2e`) and rely on the paths-filter `if:` — the job is present and blocking on every plane-touching PR, which is exactly the locked requirement.

### Data flow
1. Fixture builds a migrated temp engagement named "E2E Co", launches `create_app(tmp)` under a threaded `uvicorn.Server` on a free port.
2. Browser hits real HTTP routes: `GET /sources/new` → `POST /sources` (multipart, writes `data/users.csv`, columns auto-mapped) → `GET /controls/new` → `POST /controls` (rule spec from form) → dashboard run button → `POST /controls/sod/run` (runs full-population, writes run + workpaper) → 303 to `GET /controls/sod/runs/{run_id}` (run_view) → `GET /export` → `POST /export` (`build_bundle` → `target/bundle.zip` → `FileResponse`).
3. Test reads the downloaded zip's `manifest.json` and runs `validate_bundle(manifest)` — the same validator `bundle/assemble.py` uses — asserting `== []`.

### Expected run result (deterministic)
CSV has 2 rows. Control `sod` = rule `all(can_create eq true)` with `failure_threshold_count=0`, item key `user_id`. Both U1 and U2 have `can_create=true`, so **both** match the condition. A rule flags rows that MATCH the condition as violations (per the existing route tests: in `test_export.py` the same `can_create eq true` single-condition control produces a violation for the matching row). To get a clean "U1 fails, U2 passes" assertion, use **two** conditions like the existing `test_runs.py` fixture: `cond_column=[can_create, can_approve], cond_op=[eq, eq], cond_value=[true, true]`, logic `all`. Then only U1 (true,true) violates; U2 (true,false) passes. **The implementing agent should mirror `tests/plane/test_runs.py`'s two-condition fixture** so the run view shows `Records tested = 2`, `Failed = 1`, U1 present, U2 absent. (The single-condition snippet in the pseudo-code above must be widened to two conditions — add a second condition row via the `+ Add condition` button, which is an htmx `hx-get="/controls/_condition_row"` that appends a `rule_condition.html` partial; the test must click it and fill the second row.)

## Bundle / contract impact
**Unchanged.** This spec adds a test and CI wiring only. No producer, no manifest shape, and `contract/bundle.schema.json` are touched. The test is a *consumer* of the contract: it asserts the exported bundle still passes `validate_bundle` (the schema the app vendors), which strengthens the cardinal-rule guard rather than altering it. No raw population data enters the bundle — the test only reads `manifest.json`, exactly as `tests/plane/test_export.py` already does.

## Testing
TDD targets (the deliverable IS a test, so "tests" here means: write the failing browser test first, then make CI green):
- **New file `tests/e2e/test_smoke.py`** — `@pytest.mark.browser` `test_author_run_export_smoke`: the full flow above. Assertions: dashboard renders the engagement name; after run, `.tile-value` for Records tested = `2`, the "Operated with deficiencies" pill is visible, U1 violation row visible, U2 absent; the downloaded bundle's `manifest["schema_version"] == "1.0"`, `validate_bundle(manifest) == []`, and `sod` is present in `manifest["controls"]`.
- **New file `tests/e2e/conftest.py`** — `engagement`, `live_server` fixtures (above). Pattern-matches `tests/plane/conftest.py` for engagement setup.
- **Existing fast suite must stay green and pristine:** after the pyproject change, run `pytest -q` and confirm `tests/e2e/` is NOT collected (no `pytest-playwright` import error, no browser launch, no new warnings). Confirm the `browser` marker does not emit `PytestUnknownMarkWarning` anywhere.
- **Reuse of existing fixtures/patterns:** the CSV payload and rule-control form fields are lifted from `tests/plane/test_runs.py` / `tests/plane/test_export.py` to guarantee a known full-population result; the bundle-zip-read + `manifest["schema_version"]` assertions mirror `tests/plane/test_export.py::test_export_returns_valid_bundle`, upgraded to call `validate_bundle` directly.
- **Local verification before merge:** `pip install -e ".[plane,e2e]" && python -m playwright install chromium && pytest tests/e2e -m browser` must pass locally; `python -m ruff check .` and `python -m mypy controlflow_sdk` stay green (the new test files live under `tests/`, which mypy does not scan — `mypy controlflow_sdk` is unaffected; still run ruff on the new files).
- **New fixtures:** none beyond `engagement`/`live_server`; the CSV is an inline constant.

## Non-goals / out of scope
- Multi-browser / cross-OS matrix (Chromium only, ubuntu-latest only).
- Visual-regression / screenshot-diffing; we assert DOM text/structure, not pixels.
- Testing the Python escape-hatch (CodeMirror) authoring path, theme toggle, source refresh/versioning tabs, or the `/setup/demo` one-click demo path (demo load is an alternative the locked decision permits but we deliberately choose CSV-upload for determinism; demo coverage can be a later marked case).
- Running e2e on the fast lane, on non-plane PRs, or on the 3.11 matrix leg.
- Changing any handler, template, or the schema. Performance/load testing.
- Asserting workpaper-iframe internals beyond presence (the run-view tiles + exceptions table are the regression guard for the PR-#5-class ordering bug).

## Risks & mitigations
- **Run-trigger selector unknown** — `control_edit.html` has no Run button; the run lives on the dashboard. *Mitigation:* the implementing agent must snapshot `GET /` once and pin the exact run-form/button selector from `dashboard.html`; fall back to a row-scoped `form[action$='/controls/sod/run'] button[type=submit]`. Only if no UI run control exists, use `page.request.post(.../run)` and note it in a comment.
- **Rule semantics (violation = matched row)** — using one condition flags both rows. *Mitigation:* use the two-condition `all` fixture from `test_runs.py` so exactly U1 fails; assert `Failed = 1` and U2 absent.
- **htmx "+ Add condition" timing** — the second condition row is injected by htmx (`hx-get /controls/_condition_row`). *Mitigation:* click the add button, then `page.locator("input[name='cond_column']")` will have count 2; fill `.nth(0)`/`.nth(1)` explicitly and `expect(...).to_have_count(2)` before filling to avoid a race.
- **Threaded uvicorn startup race** — *Mitigation:* poll `server.started` with a 10s deadline before yielding; tear down with `should_exit=True` + `thread.join`. Handlers open per-request connections (learning 0002), so threading is safe.
- **Skipped-required-check blocking merges** — a `needs`+`if`-skipped job can block a "required" status check. *Mitigation:* document that branch protection should require `e2e` only via the present-when-relevant pattern (job runs on plane PRs, is absent/neutral otherwise); do not hard-require a check that reports "skipped". Verify on the first plane-touching PR.
- **`dorny/paths-filter` on push-to-main** — paths-filter needs a base ref. *Mitigation:* it works on `pull_request` out of the box; on `push: [main]` the job will compute against the prior commit. The e2e gate is about PRs, so this is acceptable; if push runs misbehave, scope the `e2e` job with `if: github.event_name == 'pull_request' && needs.changes.outputs.plane == 'true'`.
- **CI flakiness / browser download cost** — *Mitigation:* Chromium-only, `--with-deps`, single Python leg; `pip` cache already keyed on `pyproject.toml`. Test is one short flow with explicit `expect()` auto-waits (no fixed sleeps in the test body).

## Resolved open questions (2026-06-20)
- **Run-trigger selector:** pin a row-scoped `form[action$='/run'] button` by snapshotting `GET /` during implementation; confirm the dashboard `h1` text against the live render before finalizing assertions.
- **Branch protection:** the spec uses the present-when-relevant required-check pattern; the maintainer must set the GitHub required-check rule (repo-admin setting, outside the codebase). This will be called out in the PR body.
- **Second `/setup/demo` case:** non-goal for this issue.
