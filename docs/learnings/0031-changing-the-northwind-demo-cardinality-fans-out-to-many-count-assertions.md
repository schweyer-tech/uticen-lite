---
id: 0031
date: 2026-06-27
area: testing
tags: [examples, northwind, fixtures, fan-out]
status: active
supersedes: null
superseded_by: null
---

# Changing the Northwind demo's control/source count fans out to many hardcoded count assertions — audit every one

## Context

`examples/northwind-trading/` is the single demo + cold-user template + end-to-end CI
fixture, exercised by the CLI, store, web-app, and wheel test suites. Its control count
(8), source count (8), and seeded-exception total (18) are asserted as **literals** in
many independent fixtures. Adding one control + one source (the public-API
`datacenter-temperature` / `datacenter_weather`) turned all of them red at once.

## What went wrong

A single added control/source broke **7 tests across 5 files** that each hardcode the
demo's cardinality — only one (`tests/examples/test_northwind.py`) is the "obvious" owner.
The others (`test_build_cmd`, `test_import_cmd`, `test_setup`, `test_wheel_build`,
`test_import_service`) assert the same counts from their own angle (`== 8`, `(8, 8)`,
`"8 controls"`, `"8 runs"`, `len(csvs) == 8`). Fixing them one compile-error at a time is
slower and risks missing the README + `PRODUCT-MAP.md` prose counts.

## The rule

When you add or remove a **control or source** in `examples/northwind-trading/` (or change
its seeded exception counts), before trusting the suite:

1. `grep -rn` the **whole `tests/` tree** for the old literals — the control/source count,
   the exception total, and string forms (`"N controls"`, `"N runs"`) — and update every
   hit. The count lives in ≥5 files, not just `tests/examples/test_northwind.py`.
2. Update the matching prose counts in `examples/northwind-trading/README.md` (controls
   table + the "N controls / N workpapers" summary) and the Northwind row in
   `PRODUCT-MAP.md`.
3. Keep the demo's data **frozen and deterministic**: a source snapshotted from a public
   API must be committed as a frozen CSV (one-time snapshot-to-file, [0025]), so CI never
   hits the network and the exception count is stable to assert.

**Corollary — to add a capability showcase to the demo, prefer CONVERTING an existing
control over ADDING a new one.** Converting keeps the control/source counts (and their
≥5-file `== N` assertions, the README/PRODUCT-MAP count prose, and the wheel's packaged
control/CSV counts) **stable**, confining the fan-out to that one control's own
run/population/shape assertions — usually just `tests/examples/test_northwind.py` (its
`EXPECTED` count, the bundle run-count, any per-procedure assertions) plus
`tests/cli/test_build_cmd.py`'s build-summary run count. (2026-06-28: converting
`manual-je-review` to two procedures left every `== 9` count untouched and only moved the
bundle run count 9→11 via the per-procedure + aggregate fan-out [[0035]].) Keep the demo's
seeded exception **total** unchanged where possible (re-attribute, don't add exceptions) so
the grand-total assertion holds.

## Reference

- `tests/examples/test_northwind.py` (`EXPECTED` dict + exception total + manifest counts).
- `tests/cli/test_build_cmd.py`, `tests/cli/test_import_cmd.py`, `tests/plane/test_setup.py`,
  `tests/plane/test_wheel_build.py`, `tests/store/test_import_service.py` — each hardcodes the count.
- `examples/northwind-trading/README.md` + `PRODUCT-MAP.md` (prose counts).
- Same fan-out-audit shape as [0004](0004-ordering-seam-audit-positional-consumers.md) and
  [0014](0014-audit-every-singular-accessor-call-site-when-a-model-gains-a-plural.md); the frozen-snapshot
  rule is [0025](0025-one-time-snapshot-to-file-honors-the-no-live-connectors-non-goal.md).
