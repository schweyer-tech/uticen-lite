---
id: 0010
date: 2026-06-20
area: store
tags: [contract, authoring, compile, pipeline, migrations]
status: active
supersedes: null
superseded_by: null
---

# Add a richer authoring representation as store-only state plus a compile step to the existing bundle artifact — never teach the bundle the new shape

## Context

The visual pipeline (#25) introduced an entirely new authoring representation (a typed-node DAG), on top
of the existing `rule` and `python` `test_kind`s. The cardinal rule ([[0001]]) forbids changing the
bundle; [[0006]] keeps richer per-source authoring state store-only. A new *mode* is a bigger version of
the same tension: how to add a whole new way to author without the contract learning about it.

## What worked

The graph lives in a **store-only `pipeline` JSON column** (added by a store migration that bumps the
*internal* store schema, **not** `bundle.schema.json`'s `schema_version`). A **compile step** turns the
graph into the EXISTING artifacts at save/build: a pure single-source flat pipeline → a `rule_spec`
(so the simple case stays no-code in the bundle, preserving the metric); anything else → a generated
`test_code` string. Run / build / export reuse the unchanged producer path (`resolve_test_code` →
`assemble_bundle`); the store loader never reads the `pipeline` column. `contract/bundle.schema.json`
never learns the word "node", and the contract gates pass unchanged.

## The rule

Introduce a new authoring **mode/representation** as **store-only state plus a compile step** to the
existing bundle-facing artifact (`rule_spec`/`test_code`). Bump the internal store schema via a
migration; **never** bump `schema_version` and never add the new shape to `bundle.schema.json`. The
compiler is the seam — downstream (runner, `assemble_bundle`) stays untouched and the store loader must
not read the new column on the bundle path. Make the simplest case compile to the **most no-code
artifact available** (a flat single-source pipeline → `rule_spec`, not generated Python) so the
"share authored without hand-written Python" metric is preserved. Corollary of [[0001]] and [[0006]].

## Reference

- `controlflow_sdk/pipeline/compile.py` (`compile_pipeline` → `rule_spec` | `test_code`).
- `controlflow_sdk/store/migrations.py` (store schema 3→4: store-only `pipeline` column).
- `controlflow_sdk/store/repo.py` (`upsert_control` `pipeline` kwarg; loader leaves it out of the bundle path).
- Gates that stay green unchanged: `tests/test_contract_export.py`, `tests/schema/`.
- Foundations: [[0001]] (cardinal), [[0006]] (store-only authoring state).
