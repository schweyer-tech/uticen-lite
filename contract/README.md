# contract/

This directory contains the exported Uticen SDK schema contract.

## What is this?

`bundle.schema.json` is a verbatim copy of the canonical JSON Schema shipped
inside the `uticen_lite` Python package at:

    uticen_lite/schema/bundle.schema.json

It is committed here so that the Uticen app can **vendor** it without
depending on the SDK package at build time.

## Current version

Schema version: **1.0**

## How the app pins it

The Uticen app should:

1. Copy `contract/bundle.schema.json` into its own source tree (e.g.
   `src/lib/uticen-lite/bundle.schema.json`).
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
