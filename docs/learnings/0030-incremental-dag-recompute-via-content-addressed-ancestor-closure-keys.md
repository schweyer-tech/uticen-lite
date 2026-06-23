---
id: 0030
date: 2026-06-23
area: pipeline
tags: [cache, pipeline, dag, incremental, materialize, performance]
status: active
supersedes: null
superseded_by: null
---

# Recompute a DAG "from the edited step onward" by content-addressing each node's cached result on its ancestor-closure + per-source version

## Context

The step-data-inspection feature materializes the DataFrame at every pipeline node (full population) for the
inspector, the Excel exports, and the live row-count badges — and must stay fast as the author edits steps.
The requirement was "add/edit a step and recompute from that step onward, reusing unchanged upstream work."

## What went wrong / what worked

Dirty-flag / explicit graph-walk invalidation is error-prone (you must propagate "dirty" to all descendants
on every edit). Content-addressing makes invalidation fall out for free: a node's cache key is a hash of its
**ancestor-closure** (every transitive input node's data-affecting fields — id, type, config, inputs,
source_id — in topological order) **plus a version token for each source feeding that closure**. Editing a
node changes its own key and, because every descendant's closure *contains* that node, every descendant's key
— while upstream keys are unchanged. A changed source file (path+mtime+size token) busts every key downstream
of it. No dirty marking, no manual descendant walk.

The load-bearing invariant: the key must **fully determine** the output — the cached computation must not read
anything outside the key. (E.g. the materialize terminal computes violating rows as `input[mask]`, never
touching `pop.key_columns`, so two controls that hash to the same key genuinely share a correct result; a
single process-wide cache is then safe across controls.)

## The rule

For incremental recompute over a DAG, key each node's cached output on `hash(ancestor_closure_canonical +
{source_id: version_token})` and recompute exactly the nodes whose key is absent from the cache (= the edited
node and its descendants); seed the rest from cache. Canonicalize the closure deterministically (topo order,
`json.dumps(..., sort_keys=True)`) and **exclude non-data fields** (narrative/comments) so cosmetic edits
don't bust the cache. Keep the cached compute a pure function of the key — never read out-of-key state inside
it — so a shared/process-wide cache is correct across entities. Bound the cache LRU (it holds full-population
frames). Prove it with a recompute spy: editing a downstream node recomputes only it + descendants; editing
an upstream node recomputes its descendants; a changed source version busts the relevant keys.

## Reference

- `controlflow_sdk/pipeline/materialize.py` (`_step_keys` / `_ancestor_closure` / `_canonical_node`; the
  `recompute = {keys not in cache}` + seed split; LRU bound `_CACHE_MAX`).
- `controlflow_sdk/plane/routes/pipeline.py` (`_source_versions` = path+mtime+size token; process-wide
  `_STEP_CACHE`; `_materialize_full`).
- `tests/pipeline/test_materialize.py` (`test_cache_recomputes_only_edited_step_onward`,
  `test_step_keys_change_for_edited_node_and_descendants_only`, `test_source_version_change_busts_every_key`).
- Engine reuses the compiler's exact node semantics (so inspected/exported data == the real run): [[0009]].
