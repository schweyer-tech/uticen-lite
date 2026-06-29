---
id: 0039
date: 2026-06-28
area: process
tags: [planning, spec, superpowers, fan-out]
status: active
supersedes: null
superseded_by: null
---

# A plan that deviates from its spec must explicitly enumerate every DROPPED spec/design-mandated requirement — especially assertions and tests — not only the removed build steps

## Context

A design spec for the multi-procedure demo cycle mandated a concrete test: "assert the new
population `5` explicitly so the [17→5] shift is intentional." During planning, two spec
assumptions were found wrong against the code (the demo `controlplane.db` is empty → no
regeneration; `uticen-lite build` already delegates to `export_service` → no code change), and the
plan added a "Corrections" section that called out those two **removed build steps** — but it
silently omitted the spec's "assert population=5" **requirement** from every task. The
implementer followed the plan faithfully, so the assertion never got written; the suite was
green and the behavior correct, but the deliberate distinct-items-examined population was left
unlocked against regression. Only the final whole-branch review (which re-read the spec) caught
the gap.

## What went wrong

- A plan's "Corrections" / deviations note that lists only **removed work** (build steps no
  longer needed) hides **dropped requirements** (a spec-mandated assertion that still applies).
  A reader diffing plan-vs-spec sees the removed-work justified and assumes the rest carried
  over — the dropped assertion evaporates with no signal.

## The rule

When a plan deviates from its spec/design (a correction, a simplification, a scope cut),
the plan MUST **explicitly enumerate every dropped spec/design-mandated item**, separating
*removed work* (steps no longer needed — justify each) from *dropped requirements*
(assertions, tests, behaviors the spec demanded). A spec-mandated **assertion or test** is a
requirement, not "work" — never drop it silently; either keep it in a task or state in the
deviations note that it is intentionally removed and why. Before finishing a plan, re-read the
spec's Testing-strategy / "own it explicitly" sections and confirm each named assertion maps
to a task. The final whole-branch review re-reads the spec as the backstop — but the plan, not
the review, is where a dropped assertion should be caught.

## Reference

- `docs/superpowers/specs/2026-06-28-multi-procedure-northwind-showcase-design.md` — the
  "population shift" + Testing-strategy sections that mandated `assert population == 5`.
- `docs/superpowers/plans/2026-06-28-multi-procedure-northwind-showcase.md` — its "Corrections"
  section (listed removed work, omitted the dropped assertion).
- `tests/examples/test_northwind.py` — where the population/`2-1`-split assertions were added
  after the review caught the gap.
- Same spirit as [[0031]] (audit every count the demo fans out to) and [[0012]] (confirm the
  new value is correct, then lock it with an assertion — don't leave an intentional shift
  unasserted).
