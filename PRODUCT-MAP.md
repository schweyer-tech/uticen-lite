# Product Map — controlflow-sdk

> Present-state inventory of what the SDK **ships now**. Planned/unbuilt work lives in GitHub issues,
> not here. Update a row when a surface ships or changes; remove a row when a surface is retired.
> Last updated: 2026-06-19.

| Surface | Type | What it does |
| --- | --- | --- |
| `controlplane` | command / web app | Launches the local SQLite-backed FastAPI + HTMX control plane (localhost only, default `:8765`); the primary authoring surface. `pip install 'controlflow-sdk[plane]'` → `controlplane --project <dir>`. |
| Control plane — Dashboard | view | Lists controls + last-run status; entry point to author, run, and export. |
| Control plane — Source manager | view | Upload a data file into the engagement + map columns (display name, data type, is-key, include) → persisted to `controlplane.db`. |
| Control plane — Control editor | view | Author a control: metadata + framework refs + failure threshold + source binding, with a **no-code rule builder** (HTMX) or a **Python escape hatch** (CodeMirror). |
| Control plane — Run view | view | Per-run results (totals + violations table) with the **rendered workpaper embedded**. |
| Control plane — Export | view | Produces the import bundle (zip) for the ControlFlow app; validates against the contract. |
| `cflow import <yaml> [--into <dir>]` | CLI | One-time import of a YAML project into `controlplane.db`. |
| `cflow run [dir] [--control] [--at]` | CLI | Run controls over the store, full-population; write workpapers + evidence; persist runs. |
| `cflow build [dir] [--out] [--at]` | CLI | Assemble + write the import bundle from the store. |
| `cflow validate [dir]` | CLI | Light DB integrity check (deprecated stub; prefer the web app). |
| `def test(pop[, sources])` | authoring API | Python full-population test; optional 2nd arg = `{source_id: Population}` for cross-source joins. |
| `rule_spec` + no-code rule builder | authoring | Declarative **single-source** rule (12 operators, AND/OR logic, severity, description template, item-key) → violations; same execution contract as Python. |
| Bundle + `contract/bundle.schema.json` | integration contract | The import shape the ControlFlow app consumes 1:1 (definitions + run provenance; no raw population). |
| Workpaper renderer (HTML + Markdown) | output | Self-contained, audit-grade workpaper: Results, Objective & Scope, Control, Data Sources, Procedures, Exceptions, Conclusion. |
| `examples/northwind-trading/` | demo / template / fixture | An 8-control engagement (financial close / IT access / procurement, NIST 800-53 mapped); runnable demo, cold-user template, and end-to-end CI fixture. |
