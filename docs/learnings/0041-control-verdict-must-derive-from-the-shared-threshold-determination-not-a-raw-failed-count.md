---
id: 0041
date: 2026-06-28
area: render
tags: [control-plane, verdict, threshold, determination, parity, run-view, dashboard]
status: active
supersedes: null
superseded_by: null
---

# Every pass/fail verdict (and its status colours) on ANY surface must derive from the shared threshold-based `Determination` the workpaper uses — never from a raw `failed == 0`

## Context

A control passes when its exception rate is within the authored failure threshold (e.g. 15%);
the model encodes this once in `Determination` (`model/workpaper.py`), whose docstring promises
"the verdict pill and the Conclusion derive from this, so they can never disagree." The workpaper
renderer honours it. But the **control-plane** outer surfaces re-derived the verdict independently
from the run's raw counts.

## What went wrong

- `run_view.html` rendered the headline verdict with `{% if run.failed == 0 %}` and coloured the
  Failed/Pass-rate tiles off `run.failed` too. For a run within tolerance (4/30 = 13.33% ≤ 15%) the
  page showed a **red "Operated with deficiencies"** pill and red/amber tiles, while the workpaper
  iframe **embedded on the same page** showed a green "Operated effectively" conclusion — two
  opposite verdicts for one run, a few hundred pixels apart.
- The dashboard's last-run badge had the identical bug (`'pass' if failed == 0 else 'fail'`), so a
  passing control read "fail" red there too.
- Root cause: the routes (`runs.py`, `dashboard.py`) never loaded the control's `Threshold` into the
  view context, so the templates had no threshold to apply and silently fell back to zero-tolerance.
- The whole suite stayed green because no test pinned the outer verdict against a non-zero threshold.

## The rule

- Any surface that states whether a control passed/failed — the run view, the dashboard badge, a
  future history row, an email — MUST compute it via the **same** `Threshold.passes(...)` /
  `Determination` the workpaper uses. Load the control's threshold in the route and pass a computed
  `passed`/`verdict` into the template; never branch on `run.failed`/`failed == 0` in a template.
- Status **colours** are part of the verdict: tile/badge `ok`/`bad`/`warn` classes must key off the
  same `passed`, so a within-tolerance run is never painted in alarm colours.
- Surface the threshold itself next to the verdict ("Threshold: exception rate ≤ 15%") so a
  pass-with-exceptions is self-explanatory.
- Pin it: a test with a non-zero threshold and a within-tolerance run asserting the surface reads
  "Operated effectively" (and the dashboard badge reads `pass`). Render-parity kin of [[0036]].

## Reference

- `uticen_lite/plane/routes/runs.py` (run_view loads `Threshold` → `Determination`),
  `uticen_lite/plane/routes/dashboard.py` (per-row `passed`),
  `uticen_lite/plane/templates/run_view.html`, `dashboard.html`;
  `uticen_lite/model/workpaper.py` `Determination`; tests in `tests/plane/test_runs.py`
  (`test_run_view_verdict_respects_threshold`, `test_dashboard_badge_respects_threshold`).
