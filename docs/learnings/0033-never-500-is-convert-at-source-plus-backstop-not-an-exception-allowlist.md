---
id: 0033
date: 2026-06-28
area: backend
tags: [run-path, error-handling, plane, runner, pandas, defense-in-depth]
status: active
supersedes:
superseded_by:
---

# A control-run's "never a 500" guarantee is enforced by converting author/data errors to `RunnerError` at the runner AND a logged broad backstop at the route — never by an exception-type allowlist

## Context

Running a control executes **user-authored logic over user data** through pandas/pyarrow/
the filesystem, which raise exception types you will not enumerate. The web run route
(`plane/routes/runs.py`) caught only `(RunnerError, ProjectError, KeyError, IndexError)`,
and `run_control` (`runner/execute.py`) wrapped only the author-**Python-callable** path —
not the source-loading loop, nor the `rule_spec` evaluation path. The corrupted state is
**persisted in `controlplane.db`**, so once a control (or a source it shares) is broken,
every later Run click re-raises — surfacing as "all the Run buttons are broken".

## What went wrong

Ordinary authoring/editing produced states whose exceptions escaped the allowlist → raw 500:

- Cross-source `exists_in`/`not_exists_in` whose `other_source` was deleted/renamed → `ValueError` (`rules/evaluate.py`).
- A comparison op (`gt`/`ge`/`lt`/`le`) on a text-loaded column → pandas `TypeError`. (The SILENT `eq`/`in`-matches-nothing case is [[0011]]; **ordering ops RAISE** — a distinct failure mode.)
- An invalid regex pattern → `pyarrow.lib.ArrowInvalid`.
- A bound source whose backing data file is missing → `FileNotFoundError` (the `adapter.load()` loop).

`KeyError` (missing column) and the no-source `RunnerError` were already caught; the gap was
every *other* type the libraries raise.

## The rule

A "running a control never crashes the page" guarantee must be enforced two ways, not by
listing exception types:

1. **Convert at the source (runner).** In `run_control`, wrap **both** the source-loading
   loop **and** the `rule_spec` evaluation path in `try/except` that raises `RunnerError`
   (mirror the existing author-callable wrap; reuse `_clean_traceback_summary`). Then
   author/data failures carry one documented type across **both** the web app and
   `uticen-lite run`.
2. **Backstop at the boundary (route).** In the run `POST`, after the typed `except`, add a
   final `except Exception` that `logging.getLogger(__name__).exception(...)`s (so real
   server bugs stay visible) and renders the friendly page — never a 500. Wrap `run_view`
   the same way (a "never raises" helper over its whole body, incl. pre-`try` loads —
   [[0013]]).
3. **Never enumerate third-party exception types as the allowlist.** An allowlist over
   pandas/pyarrow/OS errors is a latent 500 generator; the contract is "any failure
   executing a control degrades to a friendly page," enforced by (1)+(2).
4. **Prove it per realistic corrupted state.** Add a test for each (unknown cross-source,
   ordering-op-on-text, bad regex, missing data file) asserting the run returns non-500;
   a single half-authored fixture misses the library-raised types.

Corollary (recovery): a localhost, brittle-by-design tool that persists user-corruptible
state ships a one-click factory reset (Settings ▸ "Reset to demo data" →
`store.import_service.reset_to_demo`) so a wedged store recovers without hand-editing SQLite.

## Reference

- `uticen_lite/runner/execute.py` — `run_control` source-load + `rule_spec` wraps → `RunnerError`.
- `uticen_lite/plane/routes/runs.py` — run `POST` logged backstop + `run_view` guard.
- `uticen_lite/store/import_service.py` — `reset_to_demo`.
- Tests: `tests/runner/test_execute.py`, `tests/plane/test_run_button.py`, `tests/plane/test_settings_reset.py`.
- Commit 72cd875. Extends [[0013]]; relates to [[0011]].
