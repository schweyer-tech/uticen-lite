---
id: 0001
date: 2026-06-19
area: contract
tags: [bundle, contract, controlflow-app, compatibility, trust-boundary, cardinal]
status: active
supersedes: null
superseded_by: null
---

# Cardinal rule: keep the SDK bundle-compatible with the ControlFlow app — `contract/bundle.schema.json` is the binding contract

## Context

`controlflow-sdk` exists to feed the ControlFlow SaaS app: the SDK authors + runs full-population
control tests locally and exports an **import bundle** (`bundle.zip`) that the app imports 1:1
(Settings → Imports). The single integration contract between the two products is
**`contract/bundle.schema.json`** — the app vendors/pins this schema with a version gate, so any
drift in the SDK's emitted shape silently breaks import. This is the one compatibility surface that
matters most; everything else in the SDK can change freely, the bundle shape cannot (without
coordinating both sides).

## The rule

- **`contract/bundle.schema.json` is the contract. Every change that touches the bundle manifest**
  (`bundle/assemble.py:assemble_bundle`, `bundle/archive.py:write_bundle`, or the
  control/run/violation/workpaper/source shapes they emit) **must keep the bundle validating against
  it.** The conformance tests are the gate — keep them green: `tests/test_contract_export.py` and
  `tests/schema/test_bundle_schema.py`.
- **Every bundle producer reuses `assemble_bundle` + `write_bundle` — never fork the shape.** The CLI
  (`cflow build`) and the control-plane web export (`store/export_service.py:build_bundle`) are both
  producers of the *same* manifest; a new authoring surface must funnel through the same code, not
  hand-roll a manifest.
- **Preserve the exact shape.** Required: top-level `schema_version`/`project`/`controls`; each control
  `id,title,objective,narrative,framework_refs,sources,test_code,workpaper,runs`; each run
  `run_id,executed_at,passed,failed,total,pass_rate,summary,details,control_id,provenance`. Gotchas
  that have bitten before: runs use `RunRecord.to_dict()` (the field is `total`, **not**
  `population_size`); violations are nested at `runs[].details.violations[]`; `sources[]` carry
  `id` + `{type,key_config,column_mappings}` and **no rows**; `risk` is `oneOf[object,null]`;
  provenance `path` is project-relative.
- **Trust boundary: never put raw population data in the bundle.** The manifest carries control
  *definitions* + run *provenance* (sha256 + row counts), not the data rows. No `rows`/`data`/
  `data_rows` keys. The app re-derives nothing from raw data it never received.
- **To evolve the contract, change both sides together.** Bump `schema_version`, update
  `contract/bundle.schema.json` in the SDK AND the app's vendored copy + ajv version gate in lockstep
  — never ship a shape change on one side alone.

## Reference

- `contract/bundle.schema.json` (the contract); `bundle/assemble.py` (`assemble_bundle`),
  `bundle/archive.py` (`write_bundle`); `model/run.py` (`RunRecord.to_dict()`).
- Conformance gate: `tests/test_contract_export.py`, `tests/schema/test_bundle_schema.py`.
- Producers that must stay funneled through the contract: `cli/build_cmd.py`,
  `store/export_service.py` (`build_bundle`).
