---
id: 0029
date: 2026-06-23
area: testing
tags: [bundle, trust-boundary, workpaper, datasample, tests, security]
status: active
supersedes: null
superseded_by: null
---

# A bundle trust-boundary teeth-check seeds its sentinel in an `include:false` column — the bundle zip ships a bounded workpaper sample by design

## Context

The step-data-inspection cycle added a positive teeth-check (`tests/test_steps_trust_boundary.py`)
asserting raw population never leaks into the import bundle ([[0001]]). The first cut seeded a unique
sentinel value into a normal (included) source column, built the bundle via `POST /export` after a run,
and asserted the sentinel appeared in zero bundle entries. It failed — on `workpapers/<id>.html`.

The reason is a real, intentional nuance of the trust boundary: the bundle **zip** contains the rendered
**workpaper HTML**, which embeds a **bounded `DataSample`** (capped at `MAX_SAMPLE_ROWS`, filtered to
`include=True` columns) as audit evidence. So population-derived values legitimately appear in the bundle
zip via the workpaper — what [[0001]] forbids is raw population in the schema-validated **manifest** and any
re-ingestable full-population payload, not the bounded human-readable evidence sample.

## What went wrong / what worked

`ColumnMeta.include=False` does **not** drop the column from `Population.df` (the raw frame the inspector /
materialize engine operate over) — it only excludes the column from `_sample_from_population`
(`runner/execute.py`), the sole gate between the raw frame and the workpaper. So a sentinel placed in an
`include:false` column is present in `Population.df` (and would surface in any *new* code path that
serialized the raw frame into the bundle) yet absent from the workpaper sample — giving a clean
"sentinel must be absent everywhere in the bundle" assertion with genuine teeth for the regression that
matters: a new surface accidentally wiring raw rows into the bundle.

## The rule

When writing a positive test that raw population does not leak into the bundle, seed the sentinel in a
column marked **`include:false`** (present in `Population.df`, excluded from the workpaper `DataSample`),
then assert the sentinel appears in **zero** bytes across **every** zip entry — and add a sanity assertion
that `manifest.json` parses and names the control, so a hollow/empty bundle can't pass vacuously. Do **not**
seed an included column and pattern-match forbidden tokens like `"rows"` — that false-positives on legitimate
fields (`row_count`) and on the intended workpaper sample. Corollary for any future trust-boundary reasoning:
the bundle **manifest/schema** carries no raw population, but the bundle **zip** ships a bounded, include-
filtered workpaper sample as evidence — they are different layers ([[0001]]).

## Reference

- `tests/test_steps_trust_boundary.py` (the sentinel teeth-check; `include:false` column + zero-leak +
  manifest sanity assertions).
- `uticen_lite/runner/execute.py` (`_sample_from_population` — the `include=True` filter that gates the
  workpaper sample; `MAX_SAMPLE_ROWS` cap).
- `uticen_lite/model/population.py` (`ColumnMeta.include` is sample/render metadata; the raw `df` keeps
  every column).
- Cardinal trust boundary: [[0001]].
