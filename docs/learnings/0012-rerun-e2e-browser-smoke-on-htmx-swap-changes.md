---
id: 0012
date: 2026-06-20
area: testing
tags: [control-plane, htmx, e2e, playwright]
status: active
supersedes: null
superseded_by: null
---

# Re-run and update the e2e browser smoke whenever you change an HTMX swap that restructures a form — isolated partial tests and diff review can't see the post-swap DOM

## Context

The no-code rule builder was changed (U1) so that ticking a data-source checkbox fires an
HTMX `hx-get` that re-renders every condition row, upgrading each row's column field from a
free-text `<input>` to a server-rendered `<select>` *in place*. Unit tests that rendered the
conditions partial in isolation passed; an adversarial reviewer who rendered the partial
standalone approved. CI still failed — and the failure was correct.

## What went wrong

- The pre-existing e2e smoke (issue #13) drove the *live* browser. After binding the source,
  BOTH condition rows were now `<select name="cond_column">` (row 1 upgraded in place + the
  htmx-added row 2), so a bare `select_option("select[name='cond_column']")` hit a Playwright
  strict-mode "resolved to 2 elements" violation.
- This was invisible to (a) unit tests that render the partial in isolation — they never
  perform the live swap, and (b) a diff review that confirms the new behaviour is correct —
  the duplication is a property of the *assembled, post-swap* DOM, not of the diff.
- The fix was a stale-test fix (target row 2 with `.nth(1)`), not a code fix: the new
  in-place-upgrade behaviour was the intended improvement.

## The rule

When you change how an HTMX request restructures a `plane/` form in place
(swap/refresh that upgrades, adds, or duplicates fields), the live post-swap DOM changes in
ways isolated partial-render tests and diff review cannot see. Run the full browser gate
(`pytest tests/e2e -m browser`, after `playwright install chromium`) and update its selectors
to the new DOM. Treat the control plane's e2e browser smoke as a **load-bearing** gate for
HTMX-swap changes — never dismiss an e2e strict-mode/locator failure as flaky without reading
it; it is usually reporting that the assembled DOM changed.

## Reference

- `tests/e2e/test_smoke.py` — the browser gate (issue #13).
- `controlflow_sdk/plane/templates/partials/rule_builder.html` — the `hx-get` / `hx-target` /
  `hx-swap` wiring whose behaviour change triggered this.
- Builds on [0007](0007-control-plane-editors-are-server-rendered-sub-route-tabs.md)
  (server-rendered HTMX sub-routes) and shares the spirit of
  [0009](0009-prove-generated-code-equals-the-interpreter.md) (a gate that runs the real
  thing catches what unit-level checks miss).
