# Copilot instructions for `uticen-lite`

## Build, test, and lint

- Install dev dependencies: `pip install -e ".[dev]"`
- Run fast unit/integration tests (default lane): `python -m pytest -q`
- Run a single test function: `python -m pytest -q tests/path/to/test_file.py::test_name`
- Run only browser e2e smoke tests (opt-in lane): `python -m pytest tests/e2e -m browser`
- Lint: `python -m ruff check .`
- Type-check: `python -m mypy uticen_lite`
- Build wheel: `python -m build --wheel`

Notes:
- `pyproject.toml` config ignores `tests/e2e` in the default pytest run (`addopts = "--ignore=tests/e2e"`), so browser tests must be explicitly targeted.
- Browser e2e requires the extra + browser install (see CI pattern): `pip install -e ".[plane,e2e]"` and `python -m playwright install chromium`.

## High-level architecture

`uticen-lite` is one engine with two authoring surfaces:

1. `uticen-lite` CLI (`uticen_lite/cli/`) for import/run/build.
2. `controlplane` local web app (`uticen_lite/plane/`) for browser-based authoring/runs/export.

Both surfaces share the same core pipeline:

- **Store as source of truth**: engagement state lives in `controlplane.db` (`uticen_lite/store/db.py`, `store/migrations.py`, `store/repo.py`).
- **Import path is shared**: CLI import and setup/demo import both use `store/import_service.py`.
- **Execution path is shared**: run routes/commands call `store/run_service.py`, which uses `runner/execute.py` and writes render artifacts to `target/workpapers/` and `target/evidence/`.
- **Export path is shared**: CLI build and web export both call `store/export_service.py`, which builds via `bundle/assemble.py` and zips via `bundle/archive.py`.
- **Contract gate**: bundle output must validate against `contract/bundle.schema.json` (`schema_version` is the Uticen app integration contract).

Authoring/test logic representations:

- Controls can be authored as `rule`, `python`, or `pipeline` in store state.
- Pipeline graphs are store-only authoring state that compile down to existing executable artifacts (`rule_spec`/`test_code`) at run/build time.
- Test code rendering for outputs is centralized in `rules/resolve.py` (inline `test_code` → rendered `rule_spec` → file `test_path`).

## Key repository conventions

- **Bundle compatibility is cardinal**: treat `contract/bundle.schema.json` as the single app-facing contract. Keep producers shared (`bundle/assemble.py` + `bundle/archive.py`), and do not add raw population data to bundle payloads.
- **Store-only vs bundle-facing separation is strict**: richer authoring/runtime state (e.g., pipeline graph, file-history lineage, per-procedure run internals) can evolve in store schema, but should not silently expand bundle schema.
- **Do not fork import/run/build logic by surface**: CLI and web flows intentionally reuse `store/*_service.py` layers to avoid drift.
- **Route registration order matters in `plane/app.py`**: register specific sub-routes before catch-alls to avoid shadowing (especially `/settings/*` and `/controls/{id}/pipeline*`).
- **Read strategy + learnings before non-trivial changes**: `STRATEGY.md`, `PRODUCT-MAP.md`, and `docs/learnings/INDEX.md` define active constraints/rules for this repo.

## MCP servers (recommended)

- **Playwright MCP** is useful here because `controlplane` is a local web app and many regressions are UI/HTMX-flow related.
- Typical target flow: run `controlplane --project <dir>`, then use browser automation against `http://127.0.0.1:8765` for setup, source upload/editing, control logic authoring, run views, and export flows.

## Subsystem coverage: upgrade/update path

- Upgrade capability is install-method aware (`uticen_lite/upgrade/detect.py` + `upgrade/command.py`): git-editable, pipx, and pip installs map to different upgrade commands.
- The CLI path (`uticen-lite upgrade`) runs commands inline (`uticen_lite/cli/upgrade_cmd.py`).
- The web path (`plane/routes/updates.py`) must spawn a **detached helper** (`upgrade/spawn.py`) and then shut down the current process; it must not upgrade in-process.
- Preserve egress behavior in updates routes: automatic launch-time checks are opt-in, while explicit “Check now” is user-triggered.

## Subsystem coverage: bundle/schema contract

- Treat `contract/bundle.schema.json` as the external compatibility boundary with the Uticen app.
- Keep all bundle producers funneled through shared code: `store/export_service.py` → `bundle/assemble.py` → `bundle/archive.py`.
- `assemble_bundle()` must validate output via schema validation before writing artifacts.
- Preserve trust-boundary rules from bundle assembly: include definitions/run provenance/workpapers/evidence references, but never raw population rows or local `test_path` values in the manifest.
