---
id: 0026
date: 2026-06-22
area: security
tags: [secrets-at-rest, bundle, trust-boundary, contract, store-only]
status: active
supersedes:
superseded_by:
---

# When persisting credentials in the local store, isolate them, warn loudly about plaintext-at-rest on every surface, and add a POSITIVE test that they never enter the export bundle

## Context
The URL snapshot importer persists request headers (which may carry an auth token) so
re-fetch is one click. For this brittle-by-design, single-user, localhost tool the user
approved storing them **plaintext** in `controlplane.db`. This is a new class of sensitive
state, and the cardinal rule [[0001]] forbids anything sensitive crossing into the export
bundle.

## What worked
Credentials live in a **dedicated store-only table** (`source_fetch`), isolated from the
clean `sources` row. A loud plaintext-at-rest warning callout renders on **every** surface
where creds are entered or a credentialed source is visible (the URL form + the source
Data and History tabs). The source→`SourceBinding.config`→bundle path never reads
`source_fetch`, so no URL/token reaches the bundle.

## The rule
Persisting a secret in the local store is acceptable only as an explicit, surfaced
tradeoff: (1) **isolate** it in a dedicated store-only table, never in a general row;
(2) render a **loud plaintext-at-rest warning** on *every* surface that enters or displays
the credentialed source — not just the entry form; (3) add a **positive regression test**
that builds an export bundle from a credentialed source and asserts the token, URL, and any
other store-only sensitive field are **absent from every bundle entry**. The contract
gate's structural silence is not enough — a future change to the source→binding→bundle path
could leak the value, and only a positive needle-absence test (with a teeth-check proving it
fails on a real leak) catches it. Keep all of this store-only: it never touches the bundle
`schema_version` [[0001]].

## Reference
- `uticen_lite/store/migrations.py` (`source_fetch` table, headers column comment)
- `uticen_lite/plane/templates/source_new.html`, `source_data.html`, `source_history.html` (the warning callouts)
- `tests/store/test_bundle_excludes_fetch_secrets.py` (the positive bundle-exclusion guard, with teeth-check)
- Cardinal rule [[0001]]; egress discipline [[0017]]
</content>
