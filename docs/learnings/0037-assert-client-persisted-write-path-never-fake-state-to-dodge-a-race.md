---
id: 0037
date: 2026-06-28
area: testing
tags: [control-plane, e2e, playwright, localstorage, persistence]
status: active
supersedes: null
superseded_by: null
---

# Test the WRITE path of app-persisted client state by waiting on the app's own write (`page.wait_for_function` on the stored value) — never inject the state with a manual `setItem`/`setState` to dodge an event/render race

## Context

A collapsible `<details>` section persists its open/closed state to `localStorage` via a
capture-phase `toggle` listener; an e2e test collapses a section, then reloads to assert the
on-load restore re-applies it. The HTML `toggle` event is dispatched on a queued task, so a
`page.evaluate(localStorage.getItem)` fired immediately after the click can run *before* the
listener writes — an intermittent "value not there yet" under headless Chromium.

## What went wrong

- The first fix papered over the race: read `localStorage`, and **if** it wasn't the expected
  value, `localStorage.setItem(...)` it manually before reload. The post-reload assertion (the
  READ / restore path) still passed — but the app's own **WRITE** (the toggle listener) was no
  longer asserted. A regression that broke the listener would pass via the injected state.
- "Works in real browsers" is the author grading their own work, not evidence — the load-bearing
  e2e ([[0012]]) has to actually catch the regression.

## The rule

When an e2e test exercises state the **app** persists client-side
(localStorage/sessionStorage/IndexedDB/cookie) and you hit a race where the value isn't written
yet, fix the race **deterministically by waiting on the app's write** —
`page.wait_for_function("k => window.localStorage.getItem(k) === <expected>", arg=key)` — which
removes the flake AND asserts the write path. **Never inject the state yourself**
(`setItem` / `evaluate(setState)`) to get past a race: it silently deletes the write-path
coverage, so a broken writer still goes green. Assert the write before the reload, then keep the
post-reload restore assertion — both halves (write + restore) must be covered. A manual state
injection in an e2e body is a smell: if you typed `setItem`/`localStorage.setItem` in a test,
stop and replace it with a wait-for-the-app-to-write.

## Reference

- `tests/e2e/test_smoke.py` — `test_builder_collapse_and_section_insert` (the `wait_for_function`
  on the `cflow.logic.collapse.*` key).
- `controlflow_sdk/plane/templates/logic_builder.html` — the capture-phase `toggle` listener +
  the on-load `restoreCollapse()`.
- Strengthens [[0012]] (the control-plane e2e is load-bearing — never dismiss *or paper over* a
  failure).
