# controlflow-sdk

Author and run full-population control tests; export audit-grade workpapers importable into ControlFlow.

## About

ControlFlow SDK is a pure-Python library for authoring control tests against full populations of data. Test results (violations) are formatted to import directly into the ControlFlow audit platform for collaborative exception tracking and workpaper generation.

## Installation

Since this package is not yet published to PyPI, install it in editable mode from the source repository:

```bash
pip install -e .
```

For development, include test dependencies:

```bash
pip install -e ".[dev]"
```

## Quick Start

### 1. Initialize a project

```bash
cflow init my-audit
cd my-audit
```

`init` takes a single positional argument — the directory name. This scaffolds:

- `cflow.yaml` — project metadata (name, framework, system)
- `sources.yaml` — data source definitions (CSV, Parquet, or Excel files)
- `controls/` — directory where each control lives in its own subdirectory

### 2. Add a data source

Edit `sources.yaml` to declare the data files your controls will test against.
Each source gets a unique `id` that controls reference by name:

```yaml
sources:
  - id: users
    type: file
    config:
      path: data/users.csv
      format: csv
    key_config:
      mode: single
      columns:
        - user_id
    column_mappings:
      - original_name: user_id
        display_name: User ID
        data_type: text
        is_key: true
        include: true
      - original_name: can_create
        display_name: Can Create
        data_type: boolean
        is_key: false
        include: true
      - original_name: can_approve
        display_name: Can Approve
        data_type: boolean
        is_key: false
        include: true
```

Place the matching CSV at `data/users.csv`:

```
user_id,can_create,can_approve
U001,true,false
U002,true,true
U003,false,true
```

### 3. Scaffold a control

```bash
cflow new control ctl-001
```

Or pass the project directory as a positional argument (same as `cflow init`):

```bash
cflow new control ctl-001 my-audit
```

Both forms are equivalent; `--dir` is also accepted for scripts that prefer explicit flags:

```bash
cflow new control ctl-001 --dir my-audit
```

This creates `controls/ctl-001/control.yaml` and `controls/ctl-001/test.py`.

Edit `controls/ctl-001/control.yaml` to describe the control and bind it to sources:

```yaml
id: ctl-001
title: Segregation of Duties
objective: Verify no user has both create and approve permissions.
narrative: >
  All transactions require dual approval. This control ensures
  users cannot both create and approve their own transactions.
framework_refs:
  nist:
    - AC-2
    - AC-5
sources:
  - id: users
```

Sources are listed as `- id: <source-id>`, referencing entries defined in `sources.yaml`.
Do **not** put `type`, `path`, or `key_columns` directly in `control.yaml` — those belong in `sources.yaml`.

Edit `controls/ctl-001/test.py`:

```python
def test(pop):
    """Check for users with both create and approve permissions."""
    violations = []
    for _, row in pop.df.iterrows():
        if row.get("can_create") and row.get("can_approve"):
            violations.append({
                "item_key": str(row["user_id"]),
                "description": "User has both create and approve permissions",
                "severity": "high",
                "details": {"user_id": str(row["user_id"])},
            })
    return violations
```

The `test` function:
- Receives a single `pop` argument — a `Population` object whose `.df` is a pandas DataFrame
- Returns a **list of dicts**, each with at minimum `item_key` and `description`
- Returns an empty list when all records pass

Each violation dict shape:

```python
{
    "item_key": "U002",          # required — unique row identifier
    "description": "...",        # required — why this item is a violation
    "severity": "high",          # optional — "low" | "medium" | "high" | "critical"
    "details": {"key": "value"}, # optional — additional context
}
```

### Joining across sources

A control bound to multiple sources can declare a second parameter, `sources`,
a dict of every bound source keyed by the `id` you gave it in `sources.yaml`
(the primary is included). `pop` is still the first bound source.

```python
def test(pop, sources):
    payments = pop.df                       # primary source
    invoices = sources["invoices"].df       # other bound sources, by id
    pos      = sources["purchase_orders"].df
    merged = payments.merge(invoices, on="invoice_id").merge(pos, on="po_id")
    return [
        {"item_key": r.payment_id, "description": "no matching approved PO",
         "severity": "high", "details": {"amount": r.amount_x}}
        for r in merged.itertuples() if r.status != "approved"
    ]
```

Single-argument `def test(pop)` is unchanged — the `sources` dict is only
passed when your function declares it.

### 4. Validate the control

```bash
cflow validate
```

This checks:
- `control.yaml` syntax and schema
- Source IDs in `control.yaml` resolve to entries in `sources.yaml`
- Data source file paths are referenced correctly

### 5. Run the control

```bash
cflow run
```

This will:
1. Load your control and all bound data sources
2. Execute your `test()` function against **the complete population** (no sampling)
3. Write output to the `target/` directory

#### Output Directory Structure

```
target/
├── workpapers/           # Ready-to-share, signed workpapers
│   ├── ctl-001.md        # Markdown (portable, git-friendly)
│   └── ctl-001.html      # HTML (open in browser, styled)
├── evidence/             # Raw violation data
│   └── ctl-001-violations.json  # JSON array of violations
└── run-log.json          # Immutable JSONL ledger of all runs
```

#### Running a Single Control

```bash
cflow run --control ctl-001
```

#### Custom Execution Timestamp

```bash
cflow run --at 2026-06-16T14:30:00Z
```

#### Run Provenance & Reproducibility

Every run records:
- **Execution timestamp** (`executed_at`) — ISO-8601, immutable
- **Data provenance** — sha256 hash + row count for each bound data source (recorded as the relative `path` from `sources.yaml`)
- **Run ID** — deterministic 16-char identifier (derived from control ID, timestamp, and data hashes)

### 6. Build an import bundle

Once you have runs, package them into a zip for import into ControlFlow:

```bash
cflow build
```

This reads `target/run-log.json`, assembles a validated manifest, and writes `import-bundle.zip`
(or a custom path with `--out`).

```
cflow build --out exports/my-bundle.zip
```

## Features

- `cflow init <dir>` — scaffold a new project
- `cflow new control <slug> [dir]` — scaffold a new control (positional dir, or `--dir`)
- `cflow validate [dir]` — validate all controls against `sources.yaml`
- `cflow run [dir]` — execute tests, write workpapers and evidence
- `cflow build [dir]` — package runs into an importable zip bundle

## API Reference

### `Population`

The `test` function receives a single `Population` object:

```python
def test(pop):
    # pop.df         → pandas DataFrame (rows = data records)
    # pop.columns    → list of ColumnMeta objects
    # pop.source_id  → str (data source ID from sources.yaml)
    # pop.size       → int (number of rows)
    # pop.key_columns → list[str] (key column names)
    violations = []
    for _, row in pop.df.iterrows():
        if some_condition(row):
            violations.append({
                "item_key": str(row["key_col"]),
                "description": "Reason for violation",
            })
    return violations
```

The function returns a **list of dicts** (not `Violation` objects, not `list[Population]`).

### `ColumnMeta`

Column metadata available on `pop.columns`:

```python
from controlflow_sdk import ColumnMeta

# col.original_name  → str (column name from the source file)
# col.display_name   → str (human-readable label from sources.yaml)
# col.data_type      → str ("text" | "number" | "date" | "boolean")
# col.is_key         → bool
# col.include        → bool
```

### Data types in `sources.yaml`

| `data_type` | pandas dtype | Notes |
|-------------|-------------|-------|
| `text`      | `str`       | Default; `NaN` becomes `""` |
| `number`    | `float64`   | Non-numeric values become `NaN` |
| `date`      | `datetime64`| Non-parseable values become `NaT` |
| `boolean`   | `bool`      | Recognises `true/false`, `1/0`, `yes/no` |

### Key configuration in `sources.yaml`

`key_config.type` controls how each row is uniquely identified:

- **`single`** — one column is the key (e.g. `invoice_id`). The SDK uses that column's value directly as `item_key` when recording violations.
- **`composite`** — two or more columns together identify a row. The SDK does **not** auto-concatenate them. Your `test()` function is responsible for constructing `item_key` from the relevant columns:

  ```python
  def test(pop):
      violations = []
      for _, row in pop.df.iterrows():
          item_key = f"{row['vendor_id']}|{row['invoice_id']}"
          if row["amount"] > 10000 and not row["approved"]:
              violations.append({
                  "item_key": item_key,
                  "description": "Large unapproved transaction",
              })
      return violations
  ```

The `sources.yaml` template created by `cflow init` includes commented-out examples for both modes.

## License

Apache-2.0 — see [LICENSE](LICENSE).

ControlFlow SDK is intended for use authoring control tests that integrate with the [ControlFlow](https://controlflow.app) audit platform.
