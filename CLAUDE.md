# CLAUDE.md

Guidance for Claude Code / agents working in **controlflow-sdk**. This repo is meant to be worked on
**on its own** — do not assume the ControlFlow app repo is checked out alongside it.

## What this is

`controlflow-sdk` is a standalone, Apache-2.0, pure-Python "dbt for controls". Consultants author
full-population control tests locally and export an **import bundle** the ControlFlow SaaS app
imports 1:1. Two authoring surfaces, one engine:

- **`cflow`** — the CLI: `cflow import` (YAML project → store), `cflow run`, `cflow build`, `cflow validate`.
- **`controlplane`** — a local SQLite-backed web app (`pip install '.[plane]'` → `controlplane`):
  FastAPI + HTMX, author in forms + a no-code rule builder (or a Python escape hatch), run
  full-population, view workpapers, export the bundle. SQLite (`controlplane.db`) is the source of
  truth; localhost-only; brittle-by-design.

Layout: `store/` (SQLite migrator/repo/loader/run_service/export_service) · `rules/` (rule_spec →
violations + render) · `plane/` (web app) · `runner/`,`render/`,`bundle/`,`model/`,`adapters/`
(the reused core) · `contract/bundle.schema.json` (the app integration contract) ·
`examples/northwind-trading/` (runnable demo + cold-user template + CI fixture).

## Strategy, product map & roadmap

- **Why it exists + scope/non-goals → [`STRATEGY.md`](STRATEGY.md).** Read it before brainstorming or
  planning a feature: proposals must fit the authoring-ladder north-star and respect the non-goals
  (the CCM loop, multi-tenancy, live connectors, and a general data tool are all out of scope — they
  live in or belong to the ControlFlow SaaS).
- **What's shipped now → [`PRODUCT-MAP.md`](PRODUCT-MAP.md).** Present-state inventory; update it when
  a surface ships, changes, or is retired.
- **What's next → GitHub issues** (the canonical roadmap). Priorities: **#9** no-code authoring
  usability, **#10** AI-assisted authoring, **#11** distribution + first-run onboarding; backlog
  **#12** test_code-resolution DRY, **#13** control-plane CI browser smoke test, **#14** run history.
- **Bundle contract details → [`docs/CONTRACT.md`](docs/CONTRACT.md) + `contract/bundle.schema.json`.**

## Cardinal rule — stay compatible with the ControlFlow app

**The bundle is the contract.** `contract/bundle.schema.json` is the single integration surface with
the ControlFlow app (the app vendors/pins it). Any change that touches the bundle manifest must keep
it schema-valid — the gate is `tests/test_contract_export.py` + `tests/schema/test_bundle_schema.py`.
All producers (`cflow build`, the web export) reuse `bundle/assemble.py` + `bundle/archive.py` — never
fork the shape. Never put raw population data in the bundle (trust boundary). To evolve the contract,
bump `schema_version` and change the SDK schema AND the app's vendored copy together.
**See [docs/learnings/0001](docs/learnings/0001-stay-compatible-with-the-controlflow-app.md).**

## Learnings — read before you work

`docs/learnings/INDEX.md` holds durable, binding engineering rules for this repo. **Read the relevant
entries by area before working in that area** (e.g. `plane/` → 0002; packaging → 0003; anything that
reorders a query → 0004; anything touching the bundle → 0001). After a development cycle, capture new
durable rules as `docs/learnings/NNNN-slug.md` (imperative rule + context + reference) and add a row
to `INDEX.md` (newest on top). Capture reusable RULES, not stories or one-off trivia.

## Dev workflow

- Tests: `python -m pytest -q` (keep the suite green and output pristine — no stray warnings).
- Lint/type gates: `python -m ruff check .` and `python -m mypy controlflow_sdk` must stay green.
  ruff target `py311`, line-length 100. Python floor ≥3.11.
- Install for development: `pip install -e ".[dev]"` (includes the `[plane]` web deps + test client).
  The web app needs the `[plane]` extra; `adapters` adds Parquet/Excel support.
- Packaging: verify by actually building (`python -m build --wheel`) and inspecting the wheel — a
  pyproject-parse test does not prove the wheel builds (see 0003).
- Specs/plans live in `docs/superpowers/{specs,plans}/`.

## Grounding loop (per cycle)

1. **Before brainstorming/planning** a feature: read `STRATEGY.md` and the relevant `docs/learnings/`
   entries; check the open issues so you build the right thing.
2. **Build** with the dev gates green (tests pristine, ruff, mypy) and the cardinal contract intact.
3. **After finishing a cycle:** capture durable rules in `docs/learnings/` (+ `INDEX.md`), update
   `PRODUCT-MAP.md` if a surface shipped/changed, and close/open issues to keep the roadmap honest.

## Current state (hand-off snapshot — 2026-06-19; refresh as you go)

The **control plane shipped to `main` (PR #5)**: the SQLite-backed local web app, the no-code rule
builder + Python escape hatch, and full reuse of the run/render/bundle core. **420 tests green**,
ruff/mypy clean, the wheel builds and ships the web assets. It was **end-to-end smoke-tested as an end
user**: clean venv → `pip install '[plane]'` → author a source + a rule control in the browser → run
(correct full-population result) → the audit-grade workpaper renders → export validates against
`contract/bundle.schema.json`. **Not yet published to PyPI** (install is from the repo today — issue
#11). The earlier SDK foundation (CLI, multi-source `test()`, Northwind demo, the app-side importer)
is also shipped. Next up: issues **#9 → #10 → #11**.
