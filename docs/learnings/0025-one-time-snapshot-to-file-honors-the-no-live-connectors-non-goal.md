---
id: 0025
date: 2026-06-22
area: product
tags: [non-goals, file-first, connectors, egress, strategy]
status: active
supersedes:
superseded_by:
---

# Satisfy a "bring data from a REST/remote source" request without breaking the "no live connectors / file-first" non-goal by making the fetch a one-time, user-initiated snapshot-to-file

## Context
`STRATEGY.md` lists a non-goal: *"Not live connectors. S3 / Snowflake / REST feeds are the
SaaS's job; the SDK is file-first."* A request to "expand sources to allow REST APIs"
conflicts with that on its face. The resolution shipped this cycle: fetch the URL **once**,
on an explicit user action, and **snapshot the response to a local file** that becomes the
source of truth.

## What worked
The snapshot file — not the endpoint — is what every run reads. There is no polling, no
scheduled refresh, no background read; the design has no scheduler at all. "Re-fetch" is a
deliberate button press that stages a new snapshot and routes through the human
diff→confirm review (never a silent overwrite). This honors the user's intent while keeping
the SDK file-first, so it is a *reinterpretation* of the non-goal, not a violation.

## The rule
When a request conflicts with a "no live connectors / file-first" non-goal, do **not**
design a live connector. Offer a **one-time, user-initiated fetch that snapshots to a local
file** and then flows through the identical file-source path (infer columns → version →
run). Keep egress strictly user-initiated (an explicit POST, never background/auto/polled —
consistent with [[0017]]), and route any re-fetch through the existing review→confirm diff
rather than overwriting. Surface the non-goal conflict to the user and get the
reinterpretation approved before building, rather than silently shipping a connector.

## Reference
- `STRATEGY.md` §Scope/non-goals ("Not live connectors")
- `uticen_lite/plane/fetch.py` (`fetch_snapshot` — one GET, injectable opener, no scheduler)
- `uticen_lite/plane/routes/sources.py` (`create_source_from_url`, `refetch_source` → existing refresh-confirm flow)
- `docs/superpowers/specs/2026-06-22-multi-format-sources-design.md` (§2 the reinterpretation)
- Egress discipline: [[0017]]
</content>
