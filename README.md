# controlflow-sdk

Author and run full-population control tests; export audit-grade workpapers importable into ControlFlow.

## About

ControlFlow SDK is a pure-Python library for authoring, running, and packaging control tests against
full populations of data. The **control plane** (`controlplane`) is a local web app — served entirely
on `127.0.0.1`, zero network egress — where you author sources and controls through a browser UI, run
tests, and export an import bundle for the ControlFlow audit platform.

Test results and workpapers produced by the SDK are structurally equivalent to those generated
in-app: the same section model, the same NIST 800-53 references, and the same bundle format that
lands controls, runs, exceptions, and workpapers directly into your tenant.

## Installation

```bash
pip install 'controlflow-sdk[plane]'
```

This installs the SDK plus the local web app dependencies (FastAPI, uvicorn, Jinja2). For source
installs (clone + editable):

```bash
pip install -e ".[plane]"
```

For CSV/Parquet/Excel adapter support, add `adapters`:

```bash
pip install 'controlflow-sdk[plane,adapters]'
```

## See it in action — the Northwind demo

The repo ships a complete, runnable engagement under
[`examples/northwind-trading/`](examples/northwind-trading/) — a fictional wholesale distributor with
**8 real audit controls** spanning financial close, IT access, and procurement, over 8 seeded data
extracts.

**Import it, then browse or run headless:**

```bash
git clone https://github.com/dom-schweyer-tech/controlflow-sdk
cd controlflow-sdk
pip install -e ".[plane,adapters]"

# Import the YAML project into a local engagement store
cflow import examples/northwind-trading --into demo
```

**Option A — browser authoring and viewing (recommended):**

```bash
controlplane --project demo
# → opens http://127.0.0.1:8765
```

From the dashboard you can browse sources, run controls, view workpapers, and export the bundle.

**Option B — headless CLI:**

```bash
cflow run demo --at 2026-03-31T00:00:00Z
cflow build demo --out bundle.zip --at 2026-03-31T00:00:00Z
```

```text
  RUN  Finance.GL.1    3 violation(s) / 40 records   92.5%
  RUN  Finance.GL.2    2 violation(s) / 40 records   95.0%
  RUN  Finance.AP.1    4 violation(s) / 30 records   86.67%   ← passes under 15% threshold
  RUN  Finance.AP.2    2 violation(s) / 30 records   93.33%
  RUN  Finance.AP.3    2 violation(s) / 30 records   93.33%
  RUN  IT.AC.1         3 violation(s) / 38 records   92.11%
  RUN  IT.AC.2         2 violation(s) / 38 records   94.74%
  RUN  IT.AC.3         0 violation(s) / 38 records   100.0%   ← a clean, passing control
  BUNDLE  bundle.zip  8 controls / 8 runs
```

Upload `bundle.zip` in the app at **Settings → Imports** (admin) and the 8 controls, their
workpapers, and all **18 exceptions** land in your tenant — with the NIST 800-53 references carried
through.

See the [Northwind catalog README](examples/northwind-trading/README.md) for what each control does.

## Authoring with the web app

Start the control plane in any engagement directory (or a fresh one):

```bash
controlplane --project my-audit
# → http://127.0.0.1:8765
```

### Add a data source

Go to **Sources → New source**. Upload a CSV (or Parquet/Excel with the `adapters` extra), then:

- Set the source **ID** (referenced by controls later).
- Review the **column mapping**: display name, data type (`text` / `number` / `date` / `boolean`),
  and which columns to include.
- Pick the **key configuration** — `single` (one column uniquely identifies each row) or `composite`
  (two or more columns together).

### Add a control

Go to **Controls → New control**. Fill in the metadata form:

- **ID**, **title**, **objective**, **narrative**
- **Framework references** (e.g. NIST 800-53 `AC-5`, `AC-2`)
- **Source bindings** — select one or more sources from your project

### The no-code rule builder

For single-source controls you can define the test logic without writing Python using the **rule
builder**:

- Add one or more **conditions**: `WHEN <column> <operator> <value>` (operators: `=`, `!=`, `<`,
  `>`, `<=`, `>=`, `contains`, `not contains`, `is blank`, `is not blank`)
- Chain conditions with **AND** / **OR**
- Set the **severity** (`low` / `medium` / `high` / `critical`) and a **description template** that
  can reference column values (e.g. `"Payment {payment_id} exceeds tolerance"`)

Any row that matches the rule is recorded as a violation.

### The Python escape hatch

For cross-source joins or any logic the rule builder cannot express, switch the control to
**Python mode**. Write a `test` function in the editor:

```python
def test(pop, sources):
    payments  = pop.df
    invoices  = sources["invoices"].df
    pos       = sources["purchase_orders"].df
    merged = payments.merge(invoices, on="invoice_id").merge(pos, on="po_id")
    return [
        {
            "item_key": str(r.payment_id),
            "description": "No matching approved PO",
            "severity": "high",
            "details": {"amount": r.amount},
        }
        for r in merged.itertuples()
        if r.status != "approved"
    ]
```

Single-source controls use `def test(pop)` — the `sources` dict is only passed when your function
declares it.

## Running and exporting

**From the browser:** click **Run** on any control, or **Run all** from the dashboard. Workpapers
appear under the Runs tab.

**Headless:**

```bash
cflow run my-audit                          # run all controls
cflow run my-audit --control Finance.GL.1  # run one control
cflow run my-audit --at 2026-03-31T00:00:00Z  # deterministic timestamp

cflow build my-audit --out bundle.zip      # package for ControlFlow import
```

Then upload `bundle.zip` at **Settings → Imports** in the ControlFlow app.

## Design principles

- **SQLite is the source of truth.** Every engagement is a self-contained folder:
  `controlplane.db` (metadata + runs), `data/` (uploaded source files), `target/` (workpaper HTML).
  Copy or zip the folder and you have a portable snapshot.
- **Brittle by design.** The SDK trusts the folder convention. It has no locking, no user accounts,
  and no conflict resolution — it is a local, single-user tool. The hardened multi-user experience
  (access control, concurrency, audit trails, sign-off workflows) is the paid ControlFlow app.
- **Localhost only, zero network egress.** `controlplane` listens on `127.0.0.1:8765` and never
  makes outbound connections. Client data never leaves the machine.

## Workpaper quality

`cflow run` / `controlplane` produce HTML workpapers that are **structurally equivalent and visually
close** to ControlFlow's in-app workpaper view: the same section model (Results, Objective & scope,
Control, Data sources, Procedures, Exceptions, Conclusion), sticky results bar, jump-nav sidebar, and
shared design tokens (dark enterprise palette, Inter / JetBrains Mono). The Conclusion states the
pass/fail threshold determination — a control may set `failure_threshold_pct` /
`failure_threshold_count`; otherwise zero exceptions are tolerated. Each data source renders an
interactive data table (search / sort / paginate). The document is intentionally static — no jQuery,
no CDN, no network — and degrades gracefully when JavaScript is off.

## API Reference

### `Population`

The `test` function receives a `Population` as its first argument:

```python
def test(pop):
    # pop.df          → pandas DataFrame (rows = data records)
    # pop.columns     → list of ColumnMeta objects
    # pop.source_id   → str (data source ID)
    # pop.size        → int (number of rows)
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

### `ColumnMeta`

Column metadata available on `pop.columns`:

```python
from controlflow_sdk import ColumnMeta

# col.original_name  → str (column name from the source file)
# col.display_name   → str (human-readable label)
# col.data_type      → str ("text" | "number" | "date" | "boolean")
# col.is_key         → bool
# col.include        → bool
```

### Data types

| `data_type` | pandas dtype  | Notes |
|-------------|---------------|-------|
| `text`      | `str`         | Default; `NaN` becomes `""` |
| `number`    | `float64`     | Non-numeric values become `NaN` |
| `date`      | `datetime64`  | Non-parseable values become `NaT` |
| `boolean`   | `bool`        | Recognises `true/false`, `1/0`, `yes/no` |

### Key configuration

- **`single`** — one column is the key (e.g. `invoice_id`). The SDK uses that column's value as
  `item_key` when recording violations.
- **`composite`** — two or more columns together identify a row. Your `test()` function is
  responsible for constructing `item_key`:

  ```python
  def test(pop):
      for _, row in pop.df.iterrows():
          item_key = f"{row['vendor_id']}|{row['invoice_id']}"
          if row["amount"] > 10000 and not row["approved"]:
              yield {"item_key": item_key, "description": "Large unapproved transaction"}
  ```

### Violation dict shape

```python
{
    "item_key":    "U002",          # required — unique row identifier
    "description": "...",           # required — why this item is a violation
    "severity":    "high",          # optional — "low" | "medium" | "high" | "critical"
    "details":     {"key": "value"} # optional — additional context
}
```

### Bundle / import flow

`cflow build` reads the engagement store, projects each control + its latest run + workpaper HTML into
a `manifest.json`, and writes `bundle.zip`. Upload the zip at **Settings → Imports** (admin) in the
ControlFlow app. The import is idempotent on `control_id` — re-importing the same bundle updates
existing records.

## CLI reference

| Command | Description |
|---------|-------------|
| `cflow import <src> --into <dir>` | Import a YAML project into an engagement store |
| `cflow run <dir> [--control <id>] [--at <iso>]` | Run all (or one) controls, persist results |
| `cflow build <dir> [--out <file>] [--at <iso>]` | Package runs into an importable zip bundle |
| `cflow validate [<dir>]` | Light schema check (deprecated stub; prefer the web app) |
| `controlplane [--project <dir>] [--port <n>]` | Launch the local web UI |

## License

Apache-2.0 — see [LICENSE](LICENSE).

ControlFlow SDK is intended for use authoring control tests that integrate with the
[ControlFlow](https://controlflow.app) audit platform.
