#!/usr/bin/env python3
"""Export the canonical bundle schema to contract/bundle.schema.json.

Run this script whenever the packaged schema changes to refresh the
exported contract that the ControlFlow app vendors and pins:

    python scripts/export_contract.py

The generated files are committed to the repo so that the app can vendor
contract/bundle.schema.json directly without depending on the SDK package
at build time.

CI enforces byte-identity between this file and the packaged schema via
tests/test_contract_export.py.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve paths relative to the repo root (this script lives in scripts/).
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
_PKG_SCHEMA = _REPO_ROOT / "controlflow_sdk" / "schema" / "bundle.schema.json"
_CONTRACT_DIR = _REPO_ROOT / "contract"
_CONTRACT_SCHEMA = _CONTRACT_DIR / "bundle.schema.json"
_CONTRACT_README = _CONTRACT_DIR / "README.md"


def _read_schema_version() -> str:
    """Import SCHEMA_VERSION from the package without a full install."""
    # Cheap import — the schema __init__ has no heavy dependencies.
    sys.path.insert(0, str(_REPO_ROOT))
    from controlflow_sdk.schema import SCHEMA_VERSION  # noqa: PLC0415

    return SCHEMA_VERSION


def main() -> None:
    if not _PKG_SCHEMA.exists():
        sys.exit(f"ERROR: packaged schema not found at {_PKG_SCHEMA}")

    _CONTRACT_DIR.mkdir(parents=True, exist_ok=True)

    # Copy verbatim — byte-for-byte to satisfy the CI identity test.
    shutil.copy2(_PKG_SCHEMA, _CONTRACT_SCHEMA)
    src = _PKG_SCHEMA.relative_to(_REPO_ROOT)
    dst = _CONTRACT_SCHEMA.relative_to(_REPO_ROOT)
    print(f"Copied {src} → {dst}")

    version = _read_schema_version()
    _write_readme(version)
    print(f"Wrote {_CONTRACT_README.relative_to(_REPO_ROOT)} (schema version {version})")


def _write_readme(version: str) -> None:
    content = f"""\
# contract/

This directory contains the exported ControlFlow SDK schema contract.

## What is this?

`bundle.schema.json` is a verbatim copy of the canonical JSON Schema shipped
inside the `controlflow_sdk` Python package at:

    controlflow_sdk/schema/bundle.schema.json

It is committed here so that the ControlFlow app can **vendor** it without
depending on the SDK package at build time.

## Current version

Schema version: **{version}**

## How the app pins it

The ControlFlow app should:

1. Copy `contract/bundle.schema.json` into its own source tree (e.g.
   `src/lib/controlflow-sdk/bundle.schema.json`).
2. Record the SDK git SHA or release tag it was copied from in a comment or
   a companion `vendor.json` file.
3. Re-vendor whenever the SDK ships a new schema version and update the
   recorded SHA/tag accordingly.

## Additive-only rule

The schema follows a **strict additive-only evolution policy**:

- New optional fields may be added at any time.
- Existing fields and their types are NEVER changed or removed.
- Breaking changes require a new major `SCHEMA_VERSION` and a coordinated
  migration in the app.

This guarantees that a pinned copy of `bundle.schema.json` will continue to
validate documents produced by newer SDK releases until the app explicitly
upgrades to a new major version.

## Regenerating this file

Run from the repo root:

    python scripts/export_contract.py

CI enforces byte-identity between this exported copy and the packaged schema
via `tests/test_contract_export.py`.  If the test fails, the packaged schema
has changed without the contract being re-exported — run the script above and
commit the updated file.
"""
    _CONTRACT_README.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
