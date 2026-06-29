# Workpaper revisions (round 2) — design

Follow-on to `2026-06-17-workpaper-app-parity-design.md`, applying analyst review notes.
Scope spans the **SDK renderer** and the **Uticen app** workpaper view; each note is tagged.

## Binding constraints (unchanged + one relaxation)
- SDK HTML stays a **single self-contained file**: inline `<style>`, **no external assets / no CDN**,
  every author/data string HTML-escaped, stdlib-only / Pyodide-safe.
- **Relaxation:** a **small inline `<script>`** (vanilla JS, no external deps) is now allowed **solely**
  for the data-table widget. No jQuery, no network, no eval; escape all interpolated data.

## 1. Results bar — order + metrics  (SDK + app)
- New tile order: **Records tested · Passed · Exceptions** (records first), then the verdict pill.
- **Drop the "Failed" tile in the SDK** (Failed == Exceptions for a static single run — redundant).
- App keeps Passed/Failed *and* Open exceptions (they differ over the exception lifecycle); only reorder
  so Records tested leads.

## 2. Failed vs Exceptions  (SDK)
- SDK: one finding metric only — **Exceptions**. Remove the separate Failed count from bar + anywhere
  it duplicates the exception count.

## 3 + 6. Stop repeating "full population / no sampling"  (SDK + app)
- Remove the per-section and per-procedure "full population tested — no sampling applied" statements.
- State it **once**, quietly: a single methodology line in the document header/meta (e.g. a small
  caption under the title: "Full-population test — every record evaluated"). Not in every section.

## 4. Data sources — show the data  (SDK + app)
Each bound source renders, in addition to its provenance chip (sha256 + row count + **file location**):
- A **DataTables-style interactive table** of the source rows.
- **SDK:** an inline vanilla-JS widget — pagination (page-number buttons + prev/next), a
  "Showing X to Y of Z entries" line, a search/filter box, and click-to-sort column headers.
  - **Cap: first 500 rows** embedded; if the source has more, show "showing first 500 of N rows".
  - Default page length 10 (configurable constant). Columns = the source's included column mappings
    (display names). All cell values HTML-escaped.
  - One self-contained widget; the JS is a single inline `<script>` that initializes every table on the
    page by a `data-` hook; degrade gracefully (full table visible) if JS is disabled.
- **App:** render the source data with the app's existing data-table component (TAI-style: paginated,
  searchable, sortable) in the Data sources section, plus a link to the data-source page.

## 5. Procedures — interleaved narrative blocks  (SDK + app) — BACKLOG
- Authoring-model change: let a control attach narrative segments interleaved with code.
- Deferred (analyst flagged as nice-to-have). Not in this revision. Tracked for a later cycle.

## 7. Drop Evaluation  (SDK only — keep in app)
- **SDK:** remove the auto-generated Evaluation section entirely (it was a hollow tally of exceptions;
  no human severity classification / root-cause narrative exists in SDK output).
- **App:** KEEP — it carries the auditor's severity classification (deficiency / significant deficiency /
  material weakness), the editable root-cause & pervasiveness narrative, and materiality /
  tolerable-deviation context. Ensure its heading + content read as *judgment*, not a dupe of Exceptions.

## 8 + 9. Conclusion — repurpose as the threshold determination  (SDK + app)
The Conclusion stops restating the verdict and becomes the **pass/fail rule**:
- **Threshold model** (mirror the app's `control-thresholds.ts`): a control has an optional
  `failure_threshold_pct` (0–100, % of records that may be exceptions) and/or
  `failure_threshold_count` (max absolute exceptions). A control **passes** when the exception rate is
  `<= failure_threshold_pct` **AND** the exception count is `<= failure_threshold_count` (each ignored
  when null). When **both are null**, the implicit threshold is **0** (any exception → deficiency) —
  preserves today's behavior.
- **SDK control.yaml** gains an optional `threshold:` block:
  ```yaml
  threshold:
    failure_threshold_pct: 5      # optional
    failure_threshold_count: 0    # optional
  ```
  Add to `ControlDef` + `Workpaper` models; the runner computes the determination.
- **Conclusion renders, e.g.:**
  > Threshold: control passes when the exception rate is at or below **5%** (and no more than **0**
  > exceptions). Result: **13.3%** (4 / 30 records) → **exceeds threshold → control did not operate
  > effectively.**
  When threshold is implicit-0: "Threshold: zero exceptions tolerated. Result: 4 exceptions →
  did not operate effectively."
- The verdict pill in the Results bar is derived from this same determination (single source of truth).

## Example (Northwind) updates
- Add a `threshold:` to a few controls to demonstrate **both** outcomes:
  - Give one currently-failing control a tolerance that flips it to **pass** (e.g. `failure_threshold_pct`
    high enough), to show the threshold working — but keep exception COUNTS unchanged (18 total).
  - Leave others at implicit-0 so they still fail.
  - Keep `mfa-enforcement` clean (0 exceptions → passes under any threshold).
- Re-render all 8; update the README/example README parity note.

## Tests
- SDK: update `tests/render/test_html_parity.py` for the new model — Evaluation section **absent**,
  results-bar order (records→passed→exceptions, no Failed tile), single full-population statement,
  data-table widget present (table + pager + the inline init script + 500-row cap note when capped),
  Conclusion shows the threshold determination for both a pass-under-threshold and a fail control.
- SDK: add threshold-logic unit tests (pass/fail across pct/count/implicit-0 combinations).
- App: update workpaper unit tests for the reordered results bar, threshold conclusion, removed
  full-population repetition, and the data-table in Data sources; keep Evaluation tests.

## Parity statement (README)
Unchanged stance: structurally equivalent + visually close. Note the data table is interactive
(inline vanilla JS) and capped at 500 rows in the static export.
