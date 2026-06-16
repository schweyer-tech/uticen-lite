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
cflow init --name my-controls
cd my-controls
```

This scaffolds:
- `control.yaml` — control metadata (title, objective, data sources, framework references)
- `test.py` — a test function that executes on the population

### 2. Author a control

Edit `control.yaml`:

```yaml
id: CTL-001
title: Segregation of Duties
objective: Verify no user has both create and approve permissions
narrative: |
  All transactions require dual approval. This control ensures
  users cannot both create and approve their own transactions.
framework_refs:
  nist:
    - AC-2
    - AC-5
sources:
  - id: users-permissions
    type: csv
    path: data/user-permissions.csv
    key_columns: [user_id]
```

Edit `test.py`:

```python
from controlflow_sdk import Violation, Severity, Population


def test(populations: list[Population]) -> list[Violation]:
    """Check for users with both create and approve permissions."""
    violations = []
    pop = populations[0]  # First bound data source
    
    for _, row in pop.df.iterrows():
        if row.get("can_create") and row.get("can_approve"):
            violations.append(
                Violation(
                    item_key=row["user_id"],
                    description="User has both create and approve permissions",
                    severity=Severity.HIGH,
                    details={"permissions": ["create", "approve"]},
                )
            )
    
    return violations
```

### 3. Validate the control

```bash
cflow validate
```

This checks:
- `control.yaml` syntax and schema
- `test.py` function signature and imports
- Data source paths are readable
- Output violations match the expected shape

### 4. Run the control

Once your control is authored and passes validation, execute it against the full population:

```bash
cflow run
```

This will:
1. Load your control and all bound data sources
2. Execute your `test()` function against **the complete population** (no sampling)
3. Write three types of output to the `target/` directory:
   - Markdown workpapers: `target/workpapers/<control-id>.md`
   - HTML workpapers: `target/workpapers/<control-id>.html` (open in browser)
   - Violation evidence: `target/evidence/<control-id>-violations.json`
   - Immutable run log: `target/run-log.json` (JSONL, append-only)

#### Output Directory Structure

```
target/
├── workpapers/           # Ready-to-share, signed workpapers
│   ├── CTL-001.md        # Markdown (portable, git-friendly)
│   └── CTL-001.html      # HTML (open in browser, styled)
├── evidence/             # Raw violation data
│   └── CTL-001-violations.json  # JSON array of violations
└── run-log.json          # Immutable JSONL ledger of all runs
```

#### Run Provenance & Reproducibility

Every run records:
- **Execution timestamp** (`executed_at`) — ISO-8601, immutable
- **Data provenance** — sha256 hash + row count for each bound data source
- **Run ID** — deterministic 16-char identifier (derived from control ID, timestamp, and data hashes)

This ensures every execution is:
- **Auditable** — know exactly what data was tested
- **Reproducible** — same inputs always yield the same run ID
- **Traceable** — full history in `target/run-log.json`

#### Running a Single Control

To run only one control, use `--control`:

```bash
cflow run --control CTL-001
```

#### Custom Execution Timestamp

By default, `cflow run` uses the current UTC time. To specify a fixed timestamp (e.g., for testing or historical runs):

```bash
cflow run --at 2026-06-16T14:30:00Z
```

## Features Roadmap

- **Phase 1:** Control authoring, validation, project discovery ✓
- **Phase 2 (current):** `cflow run` — execute tests against full populations, write provenanced workpapers ✓
- **Phase 3:** `cflow build` — package and export importable bundles for ControlFlow (not yet available)

## API Reference

### `Violation`

```python
from controlflow_sdk import Violation, Severity

v = Violation(
    item_key="INV-12345",
    description="Amount exceeds approval threshold",
    severity=Severity.MEDIUM,  # low, medium, high, critical
    details={"amount": 50000, "threshold": 25000},
)

# Convert to dict for serialization
d = v.to_dict()
```

### `Severity`

An enumeration: `low`, `medium` (default), `high`, `critical`.

```python
from controlflow_sdk import Severity

sev = Severity.HIGH  # or .coerce("high"), .coerce(None) → MEDIUM
```

### `Population` (type hint)

Available for type hints in test functions:

```python
from controlflow_sdk import Population

def test(populations: list[Population]) -> list[Violation]:
    pop = populations[0]
    # pop.df → pandas DataFrame
    # pop.columns → list[ColumnMeta]
    # pop.source_id → str (data source ID)
    ...
```

### `ColumnMeta` (type hint)

Available for type hints describing column metadata:

```python
from controlflow_sdk import ColumnMeta

# Used internally; exposed for type completeness
col = ColumnMeta(original_name="user_id", display_name="User ID", is_key=True)
```

## License

Apache-2.0 — see [LICENSE](LICENSE).

ControlFlow SDK is intended for use authoring control tests that integrate with the [ControlFlow](https://controlflow.app) audit platform.
