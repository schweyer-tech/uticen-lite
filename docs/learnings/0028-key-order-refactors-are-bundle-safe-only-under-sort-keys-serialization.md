---
id: 0028
date: 2026-06-22
area: contract
tags: [bundle, serialization, json, refactor, sort_keys]
status: active
supersedes: null
superseded_by: null
---

# A producer-side serialization refactor that reorders dict keys is bundle-safe ONLY because the manifest is written with `sort_keys=True`

## Context

Consolidating the duplicated `FrameworkRefs` serialization (#58) into a single `to_dict()` changed the
emitted key order at one of the four sites: `bundle/assemble._serialise_framework_refs` previously built
the dict `extra`-then-`nist`, while `FrameworkRefs.to_dict()` emits `nist`-then-`extra`. That site feeds
the **bundle manifest** — the one hard contract ([[0001]]). A naive reading says "the bundle bytes
changed, that's a contract risk." It isn't, but only for a specific, verifiable reason.

## What went wrong / what worked

The bundle manifest is written by `bundle/archive.py` via `json.dumps(manifest, indent=2,
sort_keys=True)`, which recursively normalizes **every** nested dict's key order. So any producer-side
code that builds the same key/value set in a different insertion order serializes to byte-identical
output. The refactor was safe — and the `tests/test_contract_export.py` + `tests/schema` gates proved it
stayed byte-identical. The trap would have been assuming key-order doesn't matter *without* confirming
the serializer normalizes it: if the manifest were ever written with `sort_keys=False` (or a producer
output were compared as a raw string before json round-trip), the same refactor would silently drift the
bundle.

## The rule

When a refactor reorders keys in any dict that ends up in the bundle manifest, it is safe **iff** the
serializer normalizes order — confirm `bundle/archive.py` still writes with `sort_keys=True` before
relying on it, and let the contract gate (`tests/test_contract_export.py`,
`tests/schema/test_bundle_schema.py`) prove byte-identity. Conversely: do **not** depend on insertion
order for anything bundle-facing, and if you ever drop `sort_keys=True`, treat it as a manifest-shape
change that re-exposes every producer's key order. Values still must match exactly — `sort_keys`
normalizes order, not content. Renderers here also read these dicts by key (`.get("nist")` /
`.get("extra")`), so order is irrelevant on the read side too.

## Reference

- `uticen_lite/bundle/archive.py` (`json.dumps(..., sort_keys=True)` — the normalization point).
- `uticen_lite/model/control.py` (`FrameworkRefs.to_dict`), `uticen_lite/bundle/assemble.py`
  (`_serialise_framework_refs` now delegates to it).
- Gates that proved byte-identity: `tests/test_contract_export.py`, `tests/schema/test_bundle_schema.py`.
- Cardinal contract: [[0001]]. Triage context that produced this change: [[0027]].
