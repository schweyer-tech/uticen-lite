---
id: 0006
date: 2026-06-20
area: store
tags: [store, sqlite, bundle, contract, migrations, data-model]
status: active
supersedes: null
superseded_by: null
---

# To add richer per-source/per-file state, keep the bundle-facing column as a denormalized mirror and put the new richness in a store-only table — never thread it into the bundle

## Context

The control plane gained per-file data lineage: an as-of date and version history that belong to each
uploaded file, not to the source as a whole. The bundle contract reads exactly one as-of value per
source (`sources.extract_date` → the runtime workpaper). Adding a `source_files` table risked either
breaking the cardinal contract (learning [[0001]]) or leaving the workpaper reading a stale value.

## What worked

- New per-row richness (`as_of_date`, `row_count`, `uploaded_at`, `is_current`, archived `stored_path`)
  lives in a store-only `source_files` table. The migration backfills one `is_current=1` row per
  existing source so single-file sources still show history.
- `sources.extract_date` is kept as a **denormalized mirror** of the current file's `as_of_date`,
  re-synced on EVERY write path: import (`set_initial_file`), web create, refresh-confirm
  (`record_current_file`), and the inline as-of edit (`set_current_file_asof`). The bundle path
  (`SourceBinding.to_data_source()`, `contract/bundle.schema.json`) is byte-for-byte untouched, so the
  contract gate stays green with no schema bump.

## The rule

When adding richer authoring state (per-file metadata, versions, anything the app's bundle doesn't
model) to the control plane: **put the new state in a store-only table, and keep the single
contract-facing column the bundle already reads as a denormalized mirror of the authoritative new
value — re-synced on every write path that can change it.** Never add the new fields to
`to_data_source()` or `contract/bundle.schema.json`. If you cannot keep the mirror in sync on all
write paths, the design is wrong — fix the paths, don't thread the field into the bundle.

**Corollary (SQL backfills):** to derive a basename from a known-prefixed path in a migration, use
`substr(path, length('data/') + 1)`, not `replace(path, 'data/', '')` — `replace` is replace-all and
mangles any nested path (`data/x/data/y.csv`). It is benign only while the flat `data/<name>`
convention holds.

## Reference

- `controlflow_sdk/store/migrations.py` (step 3: `source_files` + backfill).
- `controlflow_sdk/store/repo.py` (`set_initial_file` / `record_current_file` / `archive_current_file`
  / `set_current_file_asof` — the last syncs `sources.extract_date`).
- `controlflow_sdk/plane/routes/sources.py` (`create_source`, `confirm_refresh`, `update_asof`).
- Cardinal contract: learning [[0001]]; gate `tests/test_contract_export.py` + `tests/schema/test_bundle_schema.py`.
