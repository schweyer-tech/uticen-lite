# Phase 1 — Multi-source `test()` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

---

## EXECUTION RULES (read first)

- **Never ask the user for permission to continue between tasks.** Execute the full plan start to finish without interruption.
- On an unresolvable error after 2–3 attempts: note it in the task and **skip to the next task**.
- **Commit per task; do NOT push** (the controller pushes once the phase gate is green — SDK repo `main`).
- Work in the SDK repo: `/Users/dom/repos/uticen-lite`. Toolchain: `ruff`, `mypy`, `python3 -m pytest` (python3 has the editable install + dev deps). Before each commit run the full gate clean: `ruff check --fix --unsafe-fixes . && ruff format . && mypy uticen_lite && python3 -m pytest -q`.

---

**Goal:** Let a single control's `test()` access **all** of its bound sources (not just the first), enabling cross-source joins, without breaking any existing single-argument test.

**Architecture:** `run_control` already loads every bound source into a `Population`. Add a `{source_id: Population}` dict and, by inspecting the test callable's signature, pass it as an optional second argument only when the test declares it. Fully backward-compatible.

**Tech Stack:** Python 3.11+, stdlib `inspect`, pytest.

## Global Constraints

- **Backward compatibility is non-negotiable:** every existing `def test(pop)` (1-arg) test must behave exactly as before. The 294→current test suite must stay green.
- **`sources` is `dict[str, Population]` keyed by the source id** (the `id` in `sources.yaml` / the control's `sources:` list) and **includes the primary** (so `sources[primary_id] is pop`).
- **`pop` stays the primary** (first bound source).
- Pure-Python / Pyodide-safe (the runner imports adapters only via `source_for`); no new heavy deps.
- The existing `RunnerError` wrapping (with SDK-frame stripping) must still apply to multi-arg tests that raise.

---

### Task 1: Runner passes a `sources` dict to tests that accept it

**Files:**
- Modify: `uticen_lite/runner/execute.py`
- Test: `tests/runner/test_execute.py`

**Interfaces:**
- Consumes: existing `run_control(control, sources, root, executed_at)`, `Population`, `load_test_callable`.
- Produces: `_accepts_sources(fn) -> bool` (module-private helper); unchanged public `run_control` signature, new runtime behavior (calls `test_fn(primary, sources_by_id)` when the test accepts ≥2 positional params or `*args`, else `test_fn(primary)`).

- [ ] **Step 1: Write the failing tests.** Add to `tests/runner/test_execute.py`. (Reuse the file's existing fixture style for building a tmp project + sources; if helpers exist, use them.) Cover four behaviors:

```python
def test_single_arg_test_unchanged(tmp_path):
    # A control whose test.py is `def test(pop): return [...]` still runs and flags rows.
    # (Reuse the existing single-source fixture/helper in this file — assert the run succeeds
    #  and returns the expected violations, same as the pre-existing happy-path test.)
    ...

def test_two_arg_test_receives_all_sources_keyed_by_id(tmp_path):
    # A control bound to two sources "primary" and "secondary"; test.py:
    #   def test(pop, sources):
    #       assert set(sources) == {"primary", "secondary"}
    #       assert sources["primary"].df.equals(pop.df)   # primary included + identical
    #       return []
    # run_control must complete without error (the asserts inside test() would raise RunnerError otherwise).
    ...

def test_two_arg_test_can_join_across_sources(tmp_path):
    # Two CSV sources: "orders" (primary: order_id, amount) and "approvals" (order_id, approved).
    # test.py joins them and flags orders whose joined approval == "no":
    #   def test(pop, sources):
    #       import pandas as pd
    #       merged = pop.df.merge(sources["approvals"].df, on="order_id", how="left")
    #       return [{"item_key": r.order_id, "description": "unapproved", "severity": "high", "details": {}}
    #               for r in merged.itertuples() if str(r.approved) != "yes"]
    # Seed data so exactly 1 row is unapproved; assert the RunRecord has failed == 1 and that item_key.
    ...

def test_two_arg_test_that_raises_still_wrapped_as_runner_error(tmp_path):
    # def test(pop, sources): raise ValueError("boom")  -> run_control raises RunnerError naming the control + "boom"
    import pytest
    from uticen_lite.runner.execute import RunnerError
    with pytest.raises(RunnerError, match="boom"):
        ...
```

- [ ] **Step 2: Run to verify they fail.** Run: `python3 -m pytest tests/runner/test_execute.py -q`
  Expected: the two-arg tests FAIL (today `run_control` calls `test_fn(primary)` only, so `def test(pop, sources)` raises `TypeError: test() missing 1 required positional argument` — surfaced as a `RunnerError`).

- [ ] **Step 3: Implement the runner change.** In `uticen_lite/runner/execute.py`:
  - Add `import inspect` at the top (with the other stdlib imports).
  - Add the helper (near `RunnerError`):

```python
def _accepts_sources(test_fn: object) -> bool:
    """True if the author's test() declares a second positional parameter (the
    sources dict) or accepts *args — i.e. wants multi-source access."""
    try:
        params = list(inspect.signature(test_fn).parameters.values())
    except (TypeError, ValueError):
        return False
    positional = [
        p
        for p in params
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_var_positional = any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params)
    return len(positional) >= 2 or has_var_positional
```

  - In `run_control`, while looping over `control.sources` to build `populations`, also build `sources_by_id`:

```python
    populations: list[Population] = []
    prov_records: list[SourceProvenance] = []
    sources_by_id: dict[str, Population] = {}
    for binding in control.sources:
        adapter = source_for(binding, root)
        pop = adapter.load()
        prov_records.append(
            SourceProvenance(source_id=binding.id, **adapter.provenance())
        )
        populations.append(pop)
        sources_by_id[binding.id] = pop
```

  (Match the EXACT existing construction of `prov_records` / `SourceProvenance` already in the file — do not change its shape; only add the `sources_by_id[binding.id] = pop` line and the dict init.)

  - Where the test is invoked, branch on arity:

```python
    test_fn = load_test_callable(control)
    try:
        if _accepts_sources(test_fn):
            raw_result: Any = test_fn(primary, sources_by_id)
        else:
            raw_result = test_fn(primary)
    except Exception as exc:  # noqa: BLE001 — author code; wrapped below
        # (keep the existing RunnerError construction with _clean_traceback_summary unchanged)
        ...
```

- [ ] **Step 4: Run the tests + full gate.** Run: `ruff check --fix --unsafe-fixes . && ruff format . && mypy uticen_lite && python3 -m pytest -q`
  Expected: all green (the new tests pass; the pre-existing suite is unchanged).

- [ ] **Step 5: Commit** (do NOT push).

```bash
git add uticen_lite/runner/execute.py tests/runner/test_execute.py
git commit -m "feat(runner): pass a {source_id: Population} dict to tests that accept def test(pop, sources)"
```

---

### Task 2: Document multi-source authoring

**Files:**
- Modify: `README.md`
- Test: none (docs) — but verify the example in the README is itself valid Python.

- [ ] **Step 1: Update the README.** In the "Authoring a control" / `test.py` section, after the single-arg example, add a short multi-source subsection:

```markdown
### Joining across sources

A control bound to multiple sources can declare a second parameter, `sources`,
a dict of every bound source keyed by the `id` you gave it in `sources.yaml`
(the primary is included). `pop` is still the first bound source.

\`\`\`python
def test(pop, sources):
    payments = pop.df                       # primary source
    invoices = sources["invoices"].df       # other bound sources, by id
    pos      = sources["purchase_orders"].df
    merged = payments.merge(invoices, on="invoice_id").merge(pos, on="po_id")
    return [
        {"item_key": r.payment_id, "description": "no matching approved PO",
         "severity": "high", "details": {"amount": r.amount_x}}
        for r in merged.itertuples() if r.status != "approved"
    ]
\`\`\`

Single-argument `def test(pop)` is unchanged — the `sources` dict is only
passed when your function declares it.
```

- [ ] **Step 2: Verify the README example parses** as Python (sanity): extract the snippet and `python3 -c "import ast; ast.parse(open('/tmp/snippet.py').read())"` or eyeball it. Run the lint/test gate (`ruff check . && python3 -m pytest -q`) to confirm nothing else broke.

- [ ] **Step 3: Commit** (do NOT push).

```bash
git add README.md
git commit -m "docs: document multi-source def test(pop, sources)"
```

---

## Self-Review (controller, before handing off)

1. **Spec coverage:** §2 API (`test(pop, sources)`, sources keyed by id incl. primary) ✓ Task 1; runner arity-inspection ✓ Task 1 `_accepts_sources`; back-compat ✓ Task 1 test_single_arg + full suite; README ✓ Task 2.
2. **Placeholder scan:** the test bodies reference the file's existing fixture helpers (named, not invented) — the implementer reuses them; no TODO/TBD.
3. **Type consistency:** `sources_by_id: dict[str, Population]`, `_accepts_sources(fn) -> bool` used consistently; `sources[primary_id] is pop` invariant asserted in the test.
