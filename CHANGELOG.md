# Changelog

All notable changes to the ControlFlow SDK are documented here. This project adheres to [Semantic Versioning](https://semver.org/) for package releases and uses independent `schema_version` for the bundle export contract.

**Current version:** `0.1.0`  
**Current schema_version:** `1.0`

For details on versioning and compatibility policy, see [docs/CONTRACT.md](docs/CONTRACT.md).

---

## [Unreleased]

### Added

- **Upgrade & update-awareness.** The control plane detects how it was installed and can upgrade
  itself in one click — git checkout → `git pull --ff-only` + editable reinstall · pipx →
  `pipx upgrade` · pip → `pip install -U`. The same routine is available headless as
  `cflow upgrade [--check] [--yes]`. An **opt-in** "check for updates on launch" toggle
  (Settings ▸ Updates, **off by default**) preserves the control plane's zero-egress default.

---

## [0.1.0] — 2026-06-16

### Initial Release

#### Portable Core

- **Models**: `Violation`, `Severity`, `Population`, `ColumnMeta` types for type-safe control authoring
- **Project discovery**: Automatic detection of `control.yaml` + `test.py` patterns
- **Schema**: JSON schemas for control definitions, data source configs, and bundle export

#### Full Local Runner

- **`cflow run`** — Execute test functions against full populations
  - Load control metadata from `control.yaml`
  - Bind data sources (CSV, Excel, Parquet, REST APIs)
  - Execute user Python test functions with pandas DataFrames
  - Capture pass/fail results and violation details
  - Record run provenance (data hashes, timestamps, deterministic run IDs)
  
- **Output generation**:
  - Markdown workpapers with full test narrative, procedures, and results
  - HTML workpapers (styled, browser-viewable)
  - JSON violation evidence files
  - Immutable `run-log.json` (JSONL append-only ledger)

- **Execution modes**:
  - Single control via `--control <id>`
  - Full project via `cflow run` (all controls)
  - Custom execution timestamp via `--at <iso-8601>`

#### Bundle Builder

- **`cflow build`** — Package control tests for import into ControlFlow application
  - Assemble all controls, runs, workpapers, and metadata
  - Validate against `schema_version: "1.0"`
  - Generate `.cflow` archive (ZIP format) ready for upload
  - Export control definitions for external tool integration

#### CLI Commands

- **`cflow init`** — Scaffold a new project with control template
- **`cflow new`** — Add a new control to an existing project
- **`cflow validate`** — Check control YAML syntax, test function shape, and data source readability
- **`cflow run`** — Execute all controls (or one with `--control`)
- **`cflow build`** — Package and export the project bundle

#### Adapters

- **File adapters**: CSV, Excel (XLSX), Parquet
- **REST API adapter**: Fetch paginated data via HTTP
- **Data type inference**: Automatic pandas dtype detection

#### Rendering

- **Markdown renderer**: Audit-grade workpapers with narrative, procedures, and results
- **HTML renderer**: Styled output for browser viewing and printing

### Schema Contract

Bundle schema version `1.0` includes:

- **`project`** — Metadata (name, framework, system)
- **`controls`** — Array of control definitions with:
  - Test code (Python source)
  - Bound data sources with key configuration and column mappings
  - Workpaper (narrative, procedures, framework references)
  - Run history (execution results, violations, provenance)
- **`framework_refs`** — NIST 800-53 control mappings and generic framework tags
- **Run provenance** — SHA256 hashes, row counts, deterministic run IDs for reproducibility

### Limitations & Future Work

- **Not yet published to PyPI** — Install from source via `pip install -e .`
- **Python test execution only** — No R, SQL, or other languages yet
- **Local runner only** — No cloud execution service (Phase 3+)
- **No distributed execution** — Tests run serially on a single machine
- **No state persistence** — Runs do not persist across workspaces without explicit export

### Breaking Changes

None (initial release).

### Deprecations

None (initial release).

---

## Notes

### How to Update This File

1. **Before release**: Create a new section `## [X.Y.Z] — YYYY-MM-DD`
2. **Group changes**: Use subheadings (`Added`, `Changed`, `Fixed`, `Deprecated`, `Removed`, `Security`)
3. **Note schema changes**: If `schema_version` is incremented, explicitly call it out with migration details
4. **Coordinate with app**: If schema changes, ensure ControlFlow docs are updated in sync
5. **Keep unreleased section**: Always maintain an `## [Unreleased]` section for in-flight work

### Schema Version History

| SDK Version | schema_version | Notes |
|-------------|----------------|-------|
| 0.1.0       | 1.0            | Initial release; full control test & workpaper export |

---

**See also:**
- [docs/CONTRACT.md](docs/CONTRACT.md) — Versioning & compatibility policy
- [README.md](README.md) — Quick start and feature overview
- [LICENSE](LICENSE) — Apache 2.0
