# Spec: Surface per-control run history in the control plane UI (issue #14)
**Issue:** #14 · **Date:** 2026-06-20 · **Status:** approved-design

## Problem (1–3 sentences)
The dashboard shows only each control's last run and the run view shows a single run; there is no per-control run *history* in the UI even though the store already supports it via `repo.list_runs_for`. For the continuous-monitoring framing, the trend of pass-rate / exception counts across runs is the point, so we need a per-control history page (list + a lightweight trend) reachable from both the dashboard and the control page.

## Locked decisions
- History list **plus** a small trend, this round.
- Per-control history page backed by `uticen_lite/store/repo.py::list_runs_for`, linked from **both** the dashboard and the control page; each row links to its existing run view (`/controls/{control_id}/runs/{run_id}`).
- Modeled as a **server-rendered GET sub-route tab** consistent with learning 0007: the control page gains a tab strip (Definition / History); the new route is `/controls/{control_id}/history`; register the specific sub-route **before** the `/{control_id}` catch-all (0007).
- Small **trend** of pass-rate / exception counts over runs — lightweight inline SVG sparkline + minimal bars, **no JS / no chart dependency**, routed through `var(--token)` design tokens (0005), works in `[data-theme=light]`.
- Mind 0004: `list_runs_for` is **newest-first** (`ORDER BY executed_at DESC, created_at DESC`). The trend must render runs **oldest→newest left-to-right**, so reverse a copy for charting; tests use a **2+ run** fixture.
- **Bundle / contract: UNCHANGED** (read-only view over existing run data).
- Honor 0002 (sync GET via `Depends(get_conn)`), 0004 (ordering), 0005 / 0007 (UI).

## Design

### Architecture / approach
The control editor today (`/controls/{control_id}`) is a single non-tabbed page (`control_edit.html`). To satisfy the locked decision and 0007, introduce a control-page tab strip (mirroring the existing source-tabs pattern) with two tabs — **Definition** (the existing editor) and **History** (new) — and add one new GET sub-route `/controls/{control_id}/history`. All run data is read from the store; no new tables, no migrations, no writes.

A small server-side view-model helper turns the newest-first list of run dicts into a render-ready structure (chronological points for the trend, formatted timestamps for the list). Keep it pure and unit-testable, separate from the route.

### Files to create

1. **`uticen_lite/plane/templates/_control_tabs.html`** — tab strip include, mirroring `_source_tabs.html`. Uses an `active` context key:
   ```html
   <nav class="tabs">
     <a href="/controls/{{ control.id }}" class="tab {% if active == 'definition' %}active{% endif %}">Definition</a>
     <a href="/controls/{{ control.id }}/history" class="tab {% if active == 'history' %}active{% endif %}">History</a>
   </nav>
   ```
   Only render when editing an existing control (a brand-new control has no id and no history — see Definition-tab note below).

2. **`uticen_lite/plane/templates/control_history.html`** — `{% extends "base.html" %}`. Structure:
   - Crumb `← Controls` (`href="/"`) and a `page-head` with `{{ control.title }}` + `<p class="muted mono">{{ control.id }}</p>` (same header block as `control_edit.html`).
   - `{% include "_control_tabs.html" %}` with `active == 'history'`.
   - **Trend card** (only when `trend.points` has ≥1 point): a `.card` titled "Trend" containing the inline SVG sparkline + bars partial (see #3). Show a one-line `.hint` summary: "{{ trend.points|length }} runs · latest {{ trend.latest_pass_rate }}% pass".
   - **History table** in a `.table-wrap` (reuse existing table styles). Columns: **Run** (executed-at, formatted), **Result** (pass/fail badge — `badge pass` when `failed == 0` else `badge fail`, label `{{ run.pass_rate }}% pass`), **Failed** (`{{ run.failed }} / {{ run.total }}`), **Run ID** (`mono`, the 16-char id), and a shrink cell with a link **View →** to `/controls/{{ control.id }}/runs/{{ run.run_id }}`. Make the whole row's Run cell a link to the run view too. Iterate `runs` as returned (newest-first) so the list reads most-recent-first.
   - **Empty state** when `runs` is empty: `.empty-state` with "Not yet run" / "Run this control to start building history." and a POST form button to `/controls/{{ control.id }}/run` (reuse the dashboard's inline run form markup).

3. **`uticen_lite/plane/templates/partials/_run_trend.html`** — the inline-SVG trend partial (no JS). Given `trend` (chronological, oldest→newest):
   - A `<svg>` with a `viewBox` (e.g. `0 0 320 60`), `width:100%`, `height:auto`, `preserveAspectRatio="none"` is acceptable for a sparkline; keep stroke widths fixed via `vector-effect="non-scaling-stroke"`.
   - **Pass-rate sparkline:** a single `<polyline fill="none" stroke="var(--accent-primary)" .../>` whose points map each run's `pass_rate` (0–100) to y (inverted: `y = H - (pass_rate/100)*H`) and index to x (`x = i/(n-1)*W`, guard `n==1` → single point/dot). Add small `<circle>` dots per point using `var(--accent-primary)`; the final point uses `var(--status-success)` if last `failed==0` else `var(--status-critical)`.
   - **Exception-count mini-bars:** a row of `<rect>`s, one per run, height proportional to `failed / max_failed` (guard `max_failed==0` → all zero-height baseline), filled `var(--status-critical)` (or `var(--status-warning)` for partial); width derived from `n`. Use `var(--border-default)` for the baseline axis line.
   - All colors via tokens only (0005) so light theme works automatically. No `<script>`, no external assets.
   - Title each point with `<title>` for hover tooltip (e.g. `"{date} — {pass_rate}% pass, {failed} failed"`).

### Files to modify

4. **`uticen_lite/plane/routes/controls.py`**
   - Add a small pure helper (module-level), e.g.:
     ```python
     def _history_view(runs: list[dict]) -> dict[str, Any]:
         """Build the trend view-model from newest-first run dicts (0004).

         Returns chronological points (oldest→newest) for the SVG plus summary
         numbers. `runs` is left untouched (the table renders it newest-first)."""
         chrono = list(reversed(runs))  # oldest→newest for left-to-right charting
         points = [
             {"pass_rate": r["pass_rate"], "failed": r["failed"],
              "total": r["total"], "executed_at": r["executed_at"]}
             for r in chrono
         ]
         return {
             "points": points,
             "max_failed": max((p["failed"] for p in points), default=0),
             "latest_pass_rate": runs[0]["pass_rate"] if runs else None,
         }
     ```
   - Add a date-formatting helper for the list/trend tooltips. Reuse the existing approach in `sources.py` (`_fmt_stamp`) conceptually but the runs store ISO-8601 (`executed_at` like `2026-03-31T00:00:00+00:00`), so add:
     ```python
     def _fmt_executed(iso: str) -> str:
         try:
             return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")
         except (ValueError, TypeError):
             return iso or "—"
     ```
     (import `datetime` from `datetime`). Apply it to each run dict under a new key `executed_display` before rendering, and to each trend point's tooltip text (compute a `label` per point in `_history_view`).
   - Add the new route **immediately before** `@app.get("/controls/{control_id}")` (the catch-all), per 0007 — even though a 2-segment path cannot be shadowed by a 1-segment one, follow the learning's ordering rule for clarity and safety:
     ```python
     @app.get("/controls/{control_id}/history", response_class=HTMLResponse)
     def control_history(
         control_id: str,
         request: Request,
         conn: sqlite3.Connection = Depends(get_conn),  # sync GET → Depends (0002)
     ) -> Any:
         control = repo.get_control(conn, control_id)
         runs = repo.list_runs_for(conn, control_id)      # newest-first (0004)
         for r in runs:
             r["executed_display"] = _fmt_executed(r.get("executed_at", ""))
         return templates.TemplateResponse(
             request,
             "control_history.html",
             {
                 "project": repo.get_project(conn) or {"name": ""},
                 "control": control,
                 "runs": runs,
                 "trend": _history_view(runs),
                 "active": "history",
             },
         )
     ```
     This is a read-only sync GET, so `Depends(get_conn)` is correct (0002); no per-handler connection needed.

5. **`uticen_lite/plane/templates/control_edit.html`**
   - After the existing `page-head` block (around line 31) and before the `<form>`, add the tab strip but only for an existing control:
     ```html
     {% if control %}{% set active = 'definition' %}{% include "_control_tabs.html" %}{% endif %}
     ```
   - Note `edit_control` already passes the `control` dict; the `new_control` path passes `control=None`, so the tabs are correctly hidden for new controls.

6. **`uticen_lite/plane/templates/dashboard.html`**
   - In the last-run cell (lines 31–38), when `row.latest` exists, append a small **History** link next to the badge:
     ```html
     <a class="btn btn-sm btn-ghost" href="/controls/{{ row.control.id }}/history">History</a>
     ```
     Keep it inside the existing `<td>` so the dashboard stays a single table; the link is unconditional-on-having-a-control (it works even with zero runs, showing the empty state), but to keep the column tidy show it whenever a control exists. Simplest: render the History link in the same `shrink` actions cell as the Run button, or inline after the badge. Choose inline-after-badge to avoid widening the actions column.

### Data flow
1. Dashboard or control-page tab → `GET /controls/{id}/history`.
2. Route opens the shared read connection (`Depends(get_conn)`), calls `repo.list_runs_for(conn, id)` (newest-first) and `repo.get_project`.
3. Route decorates each run dict with `executed_display`, builds `trend` via `_history_view` (reverses to chronological for the SVG, computes `max_failed`, `latest_pass_rate`, per-point labels).
4. `control_history.html` renders the trend card (`_run_trend.html`) + the newest-first table; each row links to the existing `run_view` route, which already renders tiles + exceptions + workpaper.

### Key signatures / routes / SQL / templates (summary)
- New route: `GET /controls/{control_id}/history` → `control_history.html`.
- New helpers in `controls.py`: `_history_view(runs) -> dict`, `_fmt_executed(iso) -> str`.
- New templates: `_control_tabs.html`, `control_history.html`, `partials/_run_trend.html`.
- Modified templates: `control_edit.html` (add Definition/History tabs for existing controls), `dashboard.html` (add per-row History link).
- **No SQL changes** — reuse `repo.list_runs_for` / `repo.get_run` exactly as-is. **No migration.**

## Bundle / contract impact
**UNCHANGED.** This is a read-only view over existing `runs` / `violations` rows already written by `repo.insert_run`. It does not touch `bundle/assemble.py`, `bundle/archive.py`, `contract/bundle.schema.json`, `to_data_source()`, or any export path; it adds no columns and no run fields. No raw population data is surfaced beyond what the existing run view already shows (violation item keys + descriptions, already in the workpaper trust boundary). `schema_version` is not bumped. Contract gate tests (`tests/test_contract_export.py`, `tests/schema/test_bundle_schema.py`) are unaffected.

## Testing
TDD — write the route/integration tests first against the new endpoint, then the templates, then the helper unit tests.

**Unit (new file `tests/plane/test_run_history.py` or extend an existing plane test):**
- `test_history_view_orders_chronologically`: feed `_history_view` a **2-run** newest-first list (mirroring `list_runs_for` output); assert `points` is oldest→newest (reversed), `latest_pass_rate` equals the *first* (newest) input's pass_rate (guards the 0004 positional trap), and `max_failed` is the max across runs.
- `test_history_view_empty`: empty list → `points == []`, `max_failed == 0`, `latest_pass_rate is None`.
- `test_fmt_executed`: ISO string → `"YYYY-MM-DD HH:MM UTC"`; bad/empty input → fallback (`"—"` or echo).

**Route / integration (extend `tests/plane/test_runs.py`, reuse its `_rule_control` helper and the `client` fixture from `tests/plane/conftest.py`):**
- `test_history_lists_multiple_runs`: create a rule control + source, POST `/controls/sod/run` **twice** (2+ runs — 0004). GET `/controls/sod/history` → 200; assert both runs' result badges appear, the run IDs appear, and each row links to `/controls/sod/runs/{run_id}` (check `href` substrings). Assert newest-first ordering by confirming the first run-id occurrence in the HTML is the latest run's id.
  - Note: two runs in the same engagement with identical population produce the **same deterministic `run_id`** (id is `sha256(control_id+executed_at+prov_hashes)`); `executed_at` differs per POST (uses `datetime.now(UTC)`), so the ids differ. The test should not assume id equality.
- `test_history_empty_state`: create a control, do **not** run it, GET `/controls/{id}/history` → 200 and contains the "Not yet run" empty state and a run button form posting to `/controls/{id}/run`.
- `test_history_trend_renders_svg`: after 2 runs, assert the response contains `<svg` and a `<polyline` (sparkline present) and uses a token color (`var(--accent-primary)`), proving no hard-coded color (0005).
- `test_control_page_has_history_tab`: GET `/controls/{id}` (existing control) contains `href="/controls/{id}/history"` and the `tabs` nav; GET `/controls/new` does **not** contain the tabs nav (no id).
- `test_dashboard_links_to_history`: create + run a control, GET `/` contains `href="/controls/{id}/history"`.

**Existing suites to keep green:** `tests/plane/test_runs.py` (run-then-view unchanged), `tests/plane/test_controls.py` (control editor still renders), `tests/store/test_repo_runs.py` (already covers `list_runs_for` ordering with 2 runs — no change needed). Run full `python -m pytest -q` plus `ruff` + `mypy` per the dev gates (pristine, no warnings).

## Non-goals / out of scope
- No new store table, column, or migration; no change to `repo.list_runs_for` / `insert_run` shape.
- No run **deletion**, comparison/diff, filtering, pagination, or CSV/PNG export of history.
- No JS charting library, canvas, or interactive zoom — static inline SVG only.
- No change to the single-run view (`run_view.html`) or the export/bundle surfaces.
- No cross-control or portfolio-level trend dashboard (history is per-control this round).
- No real-time / auto-refresh of the history page.

## Risks & mitigations
- **0004 positional trap:** `list_runs_for` is newest-first; rendering the trend in that order would draw time backwards. Mitigation: `_history_view` reverses to chronological for the SVG while the table stays newest-first; a 2-run unit test asserts both orderings and that `latest_pass_rate` reads index 0 (newest).
- **Single-run / zero-run trend:** sparkline math divides by `n-1` and bars by `max_failed`. Mitigation: guard `n == 1` (render a single dot) and `max_failed == 0` (flat baseline); empty list renders the empty state, no SVG.
- **Route shadowing (0007):** register `/controls/{control_id}/history` before the `/{control_id}` catch-all. (Distinct segment counts mean FastAPI won't actually shadow, but ordering follows the learning and is defensive against future param-path changes.)
- **0002 thread safety:** the new endpoint is a read-only sync GET using `Depends(get_conn)` — correct; do not convert it to async or open a manual connection.
- **Light-theme regression (0005):** all SVG colors are `var(--token)`; a test asserts `var(--accent-primary)` appears in the markup so no hard-coded hex slips in.
- **`get_control` returns None** for an unknown id: template guards on `control` (the header/tabs use `control.id`); if `control is None`, render the page with the crumb and an empty state rather than 500. Add a guard in the template (`{% if control %}`) and have the empty state cover the missing-control case.

## Resolved open questions (2026-06-20)
- **Dashboard History link placement:** inline, immediately after the pass-rate badge (keeps the actions column narrow). No dedicated column.
- **Trend with a single run:** render the trend card for ≥1 data point (a lone dot is acceptable); revisit only if it reads oddly in practice.
