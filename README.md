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

## Features Roadmap

- **Phase 1 (current):** Control authoring, validation, project discovery
- **Phase 2:** `cflow run` — execute tests against live or local data
- **Phase 3:** `cflow build` — package and export audit-grade workpapers for ControlFlow import

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
