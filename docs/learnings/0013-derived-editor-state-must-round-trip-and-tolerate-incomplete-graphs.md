---
id: 0013
date: 2026-06-21
area: frontend
tags: [control-plane, editor, pipeline, htmx, refactor]
status: active
supersedes: null
superseded_by: null
---

# When an editor renders a DERIVED or in-progress representation, the round-trip and every side-effect must follow the rendered thing — not the stored thing

## Context

The "unified Logic authoring" cycle made the Builder render EVERY control as a node graph: a
stored pipeline verbatim, OR a graph *derived* on the fly from a `rule_spec` / an empty scaffold
(`derive_builder_graph`). It also moved the logic SAVE path off the metadata form onto the
Builder. Three separate bugs all came from the same root cause — code that kept pointing at the
*stored* representation or the *old* save path after the editor started showing/saving a
*derived* one.

## What went wrong (three instances, one root cause)

- **Round-trip pointed at the stored graph.** The Builder's hidden `pipeline_json` was seeded
  from the stored graph (empty for a `rule_spec` control), while the cards rendered the *derived*
  scaffold. Editing the scaffold and Saving silently discarded the edits.
- **A side-effect of the old save path was dropped.** The pre-refactor metadata save bound
  cross-source `other_source` ids via `_cross_source_ids`. The new Builder save derived sources
  only from Import nodes, so a single-Import `not_exists_in` rule left its second source unbound →
  the control compiled fine but **failed at run** (`exists_in references unknown source`).
- **Removing the old UI dropped a feature's only entry point.** Stripping the Definition
  "Test logic" card also removed the "Draft with AI" button; the endpoint still worked but had no
  UI until it was re-added in the Builder.

Plus a robustness gap the derived/in-progress states exposed: the row-count preview probe caught
only `RowCountError`, so an incomplete in-progress condition raised `RuleSpecError` and 500'd the
editor.

Recurrence (2026-06-27): the same "never 500 a half-authored control" gap reappeared on two more
boundaries. The **Run** action 500'd on a control with no bound source (`populations[0]`
`IndexError`) or no authored logic (`load_test_callable` → `ProjectError`). The **flowchart
node-click** step inspector 500'd because `_materialize_full` — documented "never raises into the
request" — ran its source loads (`_load_full_frames`/`_source_versions`) OUTSIDE its `try`, so an
unexpected failure escaped. Both now catch broadly and degrade.

## The rule

When an editor renders a DERIVED, scaffolded, or otherwise non-stored representation:

1. **Round-trip the rendered thing.** The form/serializer must submit the SAME object the cards
   were built from (the derived graph), never the stored one — or first-save silently drops edits.
2. **Re-derive every side-effect from the rendered graph, not the obvious nodes.** When you move
   or replace a save path, enumerate what the OLD path did (here: source-binding from
   cross-source `other_source`, auto-binds, defaults) and reproduce ALL of it from the new graph —
   a passing compile does not prove a passing run.
3. **Every run / inspect / preview entry point over a derived or half-authored control degrades —
   never 500.** Anything computed live (row counts, generated code, the step inspector) catches
   parse/spec errors and shows "—" or a friendly page. The **run** action on a half-authored
   control (no bound source, no logic) renders a friendly "not ready" page (422), not a stack
   trace. A helper documented "never raises into the request" wraps its **whole body including the
   pre-`try` loads** (source reads, version probes), not just the core call — a guard around only
   the inner call leaves the loads able to 500.
4. **Relocating a UI surface relocates its features.** Re-home every affordance the old surface
   carried (buttons, escape hatches), not just the primary widget.
5. **Don't let an "add step" affordance CREATE an invalid graph.** A terminal (Test) node with no
   input fails `parse_pipeline`, so the save 422s and the just-added card vanishes on the
   re-render. An insert affordance that can place a terminal with no adjacent upstream (e.g. into a
   brand-new/empty procedure section) must default the new terminal's upstream to a valid existing
   node (the last shared/Inputs-band node) so it is always wired — degrading on incomplete graphs
   (rule 3) is the backstop, but the editor should not manufacture the invalid state in the first
   place. (2026-06-28, collapsible procedure sections.)

Guard each with a test that exercises the *rendered/in-progress* path: save-a-derived-graph
persists; a cross-source rule authored in the editor actually RUNS; an incomplete condition GETs
200 not 500; a Test inserted into an empty section saves wired (no 422).

## Reference

- `uticen_lite/plane/logic_view.py` (`derive_builder_graph`) — the derived representation.
- `uticen_lite/plane/routes/pipeline.py` — `_editor_context` (round-trips the derived graph),
  `_other_source_ids` + `_save_pipeline_graph` (binds cross-source sources), `_row_counts`
  (catches `RuleSpecError`); `_materialize_full` + `step_data` (whole-body guards, 2026-06-27).
- `uticen_lite/runner/execute.py` (empty-population guard) + `uticen_lite/plane/routes/runs.py`
  (catches `RunnerError`/`ProjectError` → `run_error.html`, 422) — the run boundary degrades (2026-06-27).
- Builds on [0010](0010-new-authoring-representation-compiles-to-the-existing-artifact.md)
  (store-only graph compiles to the artifact) and is verified by the gate in
  [0012](0012-rerun-e2e-browser-smoke-on-htmx-swap-changes.md).
