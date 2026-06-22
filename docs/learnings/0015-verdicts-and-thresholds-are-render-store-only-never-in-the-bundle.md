---
id: 0015
date: 2026-06-22
area: contract
tags: [bundle, workpaper, threshold, determination, store, render]
status: active
supersedes: null
superseded_by: null
---

# A control's threshold and pass/fail determination are render + store concerns — never serialize them into the bundle

## Context

Multi-procedure controls (one control forking into N terminal Test nodes) needed **per-procedure**
pass/fail thresholds. The bundle contract (`contract/bundle.schema.json`) carries only raw run results
(`$defs/run`: `passed/failed/total/pass_rate/violations`) plus an **unbounded** `workpaper.procedures`
array of `{title, narrative, test_code, result}`. There is **no `threshold` or `determination` field
anywhere in the bundle**. So per-procedure thresholds + an "any procedure fails ⇒ fail" roll-up shipped
with **zero `bundle.schema.json` change**: the threshold lives in the terminal node's store-only config,
and the determination is computed at render/app time.

## The rule

A control's **threshold** and its **pass/fail determination** (verdict) are control-plane + render
concerns — **never serialize them into any bundle dict**. To add per-X verdict semantics (per-procedure,
per-anything): store the **inputs** (counts, thresholds) in store-only state and **compute the verdict at
render time**; keep `schema_version` frozen and the bundle carrying only raw results + the procedures
array. The bundle stays the trust/contract boundary — it transports evidence, not opinions about it. If a
consumer (the ControlFlow app) ever genuinely needs the verdict in the bundle, that is a **coordinated
`schema_version` bump on both sides**, never a unilateral field add. Corollary: when adding a per-X
breakdown, group the existing store rows by the new key (here `runs.procedure_id`) and emit one
`procedure` per group — do not invent a new bundle shape.

## Reference

- `contract/bundle.schema.json` — `$defs/run`, `$defs/procedure` (`{title, narrative, test_code,
  result}`), `$defs/workpaper.procedures` (unbounded, no threshold).
- `controlflow_sdk/model/workpaper.py` — `Procedure.determination`, `Workpaper.determination` (any-fails
  roll-up); `Procedure.to_dict()` deliberately omits `threshold`/`determination`.
- `controlflow_sdk/bundle/assemble.py` (`_build_workpaper` groups runs by `procedure_id`);
  `controlflow_sdk/store/migrations.py` (store-only `runs.procedure_id`).
- Extends the cardinal rule [[0001]] and [[0010]] (store-only state compiles to the existing artifact).
