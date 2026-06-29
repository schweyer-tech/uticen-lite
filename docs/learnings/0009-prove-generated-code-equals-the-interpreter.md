---
id: 0009
date: 2026-06-20
area: testing
tags: [codegen, rules, pipeline, contract]
status: active
supersedes: null
superseded_by: null
---

# When a code path generates executable code from a spec, prove it behaviorally equals the interpreter by exec-and-compare on real fixtures

## Context

Two surfaces generate Python that the Uticen app later re-runs from the bundle: the rule grammar
(#9) renders a `rule_spec` into a `test_code` string (`rules/render_rule.py`), and the pipeline compiler
(#25) walks a graph into either a `rule_spec` or a generated `test(pop, sources)` string
(`pipeline/compile.py`). A divergence between the generated code and the in-process evaluator
(`rules/evaluate.py`) is a **silent correctness bug that the bundle ships** — the workpaper and the
app would compute different exceptions from the "same" control.

## What worked / what went wrong

The guard is an **equivalence test**: build the artifact, `exec()` the generated code against shared
fixtures, and assert byte-identical violations to the canonical interpreter. #9 established render ≡
evaluate; #25 reused it (`test_terminated_access_compile_equivalence`). It paid off immediately — the
adversarial review on #25 found an `any`-logic compile path that disagreed with the Python path; the
regression `test_filter_any_rule_path_would_disagree_with_python_path` now pins it. A passing `rule_spec`
shape is **not** proof that the rendered/compiled Python matches.

## The rule

Any code path that **generates executable code** from a higher-level spec or graph must ship an
**equivalence test** that exec-and-compares the generated output against the canonical interpreter on
representative fixtures — explicitly covering multi-condition `any`/`all` and cross-source cases, not
just the happy single-condition path. Treat a generated-vs-interpreter divergence as a release blocker.
Keep one source of truth for the shared lowering (here: `render_rule._mask_expr` is parameterized by
`frame=` so the compiler reuses the exact condition→pandas expression instead of forking it).

## Reference

- `tests/pipeline/test_compile.py` (`test_terminated_access_compile_equivalence`,
  `test_filter_any_rule_path_would_disagree_with_python_path`).
- `uticen_lite/rules/render_rule.py` (`_mask_expr`, `frame=` param — single source of truth).
- `uticen_lite/rules/evaluate.py` (the canonical interpreter the generated code must match).
- The contract this protects: [[0001]].
