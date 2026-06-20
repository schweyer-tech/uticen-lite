# Learnings Index — controlflow-sdk

Durable, hard-won engineering rules harvested from this repo. Each row links to a file with full
context, the enforceable rule, and a reference. Read the relevant file by ID before doing work in
that area; treat active rules as binding. Newest entries are on top.

> ⚑ **[0001](0001-stay-compatible-with-the-controlflow-app.md) is the cardinal rule** — read it before
> any change that touches the bundle. The SDK exists to feed the ControlFlow app; staying
> bundle-compatible (`contract/bundle.schema.json`) is the one compatibility surface that must never
> silently drift.

| ID | Date | Area | Rule (one line) | Status |
| --- | --- | --- | --- | --- |
| [0007](0007-control-plane-editors-are-server-rendered-sub-route-tabs.md) | 2026-06-20 | frontend | Model a multi-section control-plane editor as server-rendered `GET` sub-route tabs (`/x`, `/x/data`, `/x/history`) sharing one `_*_tabs.html` nav include + an `active` key — not client-side JS tabs; register the specific sub-routes so the `/{id}` route can't shadow them. | active |
| [0006](0006-evolve-source-state-without-touching-the-bundle.md) | 2026-06-20 | store | To add richer per-source/per-file authoring state, put it in a store-only table and keep the single bundle-facing column (`sources.extract_date`) as a denormalized MIRROR of the authoritative value, re-synced on every write path — never thread new fields into `to_data_source()`/`bundle.schema.json`. Corollary: SQL backfills strip a known prefix with `substr(path, length('data/')+1)`, not replace-all. | active |
| [0005](0005-control-plane-reuses-workpaper-design-tokens.md) | 2026-06-19 | frontend | The control-plane UI (`plane/static/app.css`) and the workpaper renderer (`render/html.py`) share one design language but NOT a stylesheet — keep the duplicated palettes in sync, and route every color through a `var(--token)` so theming (the `[data-theme=light]` override + `--cm-*` CodeMirror tokens) stays a one-place change; apply the saved theme before first paint. | active |
| [0004](0004-ordering-seam-audit-positional-consumers.md) | 2026-06-19 | data-integrity | When a query's sort order changes (`ORDER BY ... DESC` for a newest-first UI), audit every positional consumer (`x[-1]`/`x[0]`) — a reorder silently breaks "pick the latest/first" code; single-record fixtures can't catch it, so use a 2+ record fixture + a whole-branch review. | active |
| [0003](0003-hatch-wheel-no-force-include-package-data.md) | 2026-06-19 | packaging | Hatchling `packages = ["pkg"]` already ships package-internal data files; `force-include`-ing a path already inside the package fails the wheel build (duplicate archive path). Force-include only out-of-package/VCS-ignored files; verify packaging by BUILDING the wheel, not by parsing pyproject. **Corollary:** to ship out-of-package data (a demo/example) to pip users, force-include it into the package — but force-include skips editable installs, so runtime readers MUST resolve packaged-first with a repo-path fallback, and prove it from a clean-venv wheel install outside the repo. | active |
| [0002](0002-fastapi-sqlite-per-handler-connection.md) | 2026-06-19 | backend | FastAPI runs a sync `Depends` generator in a threadpool but an `async def` handler in the loop thread → a shared `sqlite3` conn throws cross-thread; open a per-handler connection (try/finally) in async/writing handlers, keep `Depends(get_conn)` for sync GETs. Also: Starlette ≥1.3 `TemplateResponse(request, name, ctx)`. | active |
| [0001](0001-stay-compatible-with-the-controlflow-app.md) | 2026-06-19 | contract | ⚑ Cardinal: `contract/bundle.schema.json` is the binding contract with the ControlFlow app — every manifest-touching change must keep the bundle schema-valid (gate: `tests/test_contract_export.py` + `tests/schema/test_bundle_schema.py`); all producers reuse `assemble_bundle`/`write_bundle`; never put raw population in the bundle; evolve the schema on both sides together. | active |
