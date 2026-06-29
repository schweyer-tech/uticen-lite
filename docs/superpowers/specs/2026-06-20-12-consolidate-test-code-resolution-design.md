# Spec: Consolidate duplicated test_code resolution (issue #12)
**Issue:** #12 · **Date:** 2026-06-20 · **Status:** approved-design

## Problem (1–3 sentences)
"Resolve a control's test_code for output" is implemented twice — `bundle/assemble.py::_resolve_test_code` (priority inline → rule → file → "") and `store/run_service.py` inline logic (priority rule → inline, with `None` deferring the disk read to assemble time). They agree today only because a control never carries both `test_code` and `rule_spec` at once, so this is the single most likely future drift point in the bundle pipeline. Extract one shared resolver, used by both producers, with `run_service` keeping only its thin "None means read-from-disk at assemble time" wrapper.

## Locked decisions
- Extract a single shared resolver. It needs `rule_to_text` (`rules/render_rule.py`), so it lives in `rules/` (not `model/control.py`, which must stay dependency-free). Confirmed by reading the import graph (see Design).
- Preserve EXACT current priority semantics: **inline (`test_code`) → rule (`rule_spec`) → file (`test_path`) → "" (empty string)**. This is the canonical order from `assemble._resolve_test_code`.
- Keep `run_service`'s "**None means read-from-disk at assemble time**" behavior as a thin wrapper over the shared resolver.
- Pure refactor: behavior must be byte-identical; bundle output UNCHANGED.
- Honor cardinal rule 0001: the contract export tests (`tests/test_contract_export.py`, `tests/schema/test_bundle_schema.py`) and `tests/bundle/test_assemble.py` must still pass unchanged.

## Design

### Import-graph confirmation (why `rules/`)
- `uticen_lite/rules/render_rule.py` imports only from `rules/spec.py`. `rules/spec.py` imports only stdlib. So `rules/` has **no** dependency on `model/`, `bundle/`, or `store/`.
- `model/control.py` imports **nothing** from `uticen_lite` (verified: `grep uticen_lite model/control.py` → none). Putting the resolver there would force `model → rules`, breaking the "model is a leaf data holder" invariant — rejected, as the locked decision anticipated.
- `bundle/assemble.py` already does a lazy `from uticen_lite.rules.render_rule import rule_to_text` inside `_resolve_test_code`. `store/run_service.py` already imports `rule_to_text` + `parse_rule_spec` at module top. So both producers already depend on `rules/`; a resolver in `rules/` introduces **no new edges and no cycle**.

The resolver takes a `ControlDef` but must not import `model/control.py` at module scope (would create `rules → model → (nothing)`, harmless, but we keep `rules/` a leaf to be safe and to avoid any future `model → rules` temptation). Use a `TYPE_CHECKING`-only import of `ControlDef` plus duck-typed attribute access (`control.test_code`, `getattr(control, "rule_spec", None)`, `control.test_path`) — exactly mirroring the current `_resolve_test_code`, which already uses `getattr` for `rule_spec`.

### Component: new shared resolver
**Create `uticen_lite/rules/resolve.py`:**

```python
"""Single source of truth for resolving a control's test_code for output.

Priority: inline (test_code) → rule (rule_spec) → file (test_path) → "".
Used by bundle.assemble (bundle output) and store.run_service (workpaper text)
so the two producers cannot drift. See issue #12 / learning 0001.
"""
from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from uticen_lite.rules.render_rule import rule_to_text
from uticen_lite.rules.spec import parse_rule_spec

if TYPE_CHECKING:
    from uticen_lite.model.control import ControlDef


def resolve_test_code(control: ControlDef) -> str:
    """Resolve a control's test_code with priority inline → rule → file → "".

    1. ``control.test_code`` — already-inlined Python source.
    2. ``control.rule_spec`` — declarative rule; rendered to human-readable text.
    3. ``control.test_path`` — path to a .py file; content read from disk.
    4. Empty string fallback.
    """
    if control.test_code is not None:
        return control.test_code
    rule_spec = getattr(control, "rule_spec", None)
    if rule_spec is not None:
        return rule_to_text(parse_rule_spec(rule_spec))
    if control.test_path:
        return pathlib.Path(control.test_path).read_text(encoding="utf-8")
    return ""
```

This is the **byte-identical body** of the current `bundle/assemble.py::_resolve_test_code`, with the two lazy imports promoted to module top (safe — no cycle, confirmed above) and `rule_to_text`/`parse_rule_spec` imported once.

### Component: `bundle/assemble.py` — delegate
- **Modify** `uticen_lite/bundle/assemble.py`:
  - Delete the `_resolve_test_code` function (lines 115–134).
  - Add module-top import: `from uticen_lite.rules.resolve import resolve_test_code`.
  - In `_build_control_block` (currently line 147) change `test_code = _resolve_test_code(control)` → `test_code = resolve_test_code(control)`.
  - Update the `_build_control_block` docstring reference from `:func:`_resolve_test_code`` to `:func:`~uticen_lite.rules.resolve.resolve_test_code``.
  - Remove the now-unused `import pathlib` only if nothing else in the file uses it. **Verify before removing** (grep `pathlib` in the file) — keep it if still referenced, drop it if not (ruff will flag an unused import otherwise).

### Component: `store/run_service.py` — thin wrapper preserving "None → read-from-disk"
- **Modify** `uticen_lite/store/run_service.py`:
  - Replace the top imports `from uticen_lite.rules.render_rule import rule_to_text` and `from uticen_lite.rules.spec import parse_rule_spec` with `from uticen_lite.rules.resolve import resolve_test_code` (drop the two now-unused imports).
  - Replace the resolution block (current lines 53–61):

    ```python
    resolved_test_code: str | None
    if control.test_kind == "rule" and control.rule_spec is not None:
        resolved_test_code = rule_to_text(parse_rule_spec(control.rule_spec))
    else:
        resolved_test_code = control.test_code  # str | None — None → assemble reads test_path
    ```

    with:

    ```python
    # Resolve the test code shown in the workpaper.  Inline-python controls
    # already carry test_code; rule controls have no .py file so we render the
    # rule to readable text.  File-based controls (test_code is None) keep None
    # here so Workpaper.assemble defers the disk read to assemble time.
    resolved_test_code: str | None
    if control.test_code is not None:
        resolved_test_code = control.test_code
    elif control.rule_spec is not None:
        resolved_test_code = resolve_test_code(control)  # renders rule → text
    else:
        resolved_test_code = None  # file-based — Workpaper.assemble reads test_path
    ```

**Why this is byte-identical (the load-bearing argument):**
- `run_service` operates only on **store-loaded** controls (`load_project_from_store`), where `test_path` is always `""` (loader hard-codes `test_path=""`, line 67). So the resolver's file branch is never reachable from this path; "None means read-from-disk" is run_service's own concern, not the shared resolver's.
- Store-loaded controls are mutually exclusive: rule controls have `test_code=None, rule_spec=set`; inline controls have `test_code=set, rule_spec=None` (confirmed via loader.py 59–69 + the no-code/inline UI). So:
  - **Inline control** (`test_code` set): new code returns `control.test_code` — same as old `else` branch.
  - **Rule control** (`test_code` None, `rule_spec` set): new code calls `resolve_test_code`, whose inline branch is skipped (`test_code is None`) and returns `rule_to_text(parse_rule_spec(control.rule_spec))` — **identical** to the old rule branch. (Old branch also gated on `test_kind == "rule"`, which is `True` iff `rule_spec is not None` per `ControlDef.test_kind`, so the gate is equivalent.)
  - **Neither** (`test_code` None, `rule_spec` None): new code returns `None` — same as old `else` branch (`control.test_code` was `None`). `Workpaper.assemble` then defers, reading `test_path` (which is `""` for store controls → empty) at assemble time, unchanged.
- The only ordering difference between the two original call sites (assemble: inline-first; run_service: rule-first) collapses to the same result because store controls never set both. The refactor adopts the **canonical inline-first order** in both places, so even a hypothetical both-set control now resolves identically in both producers — which is the whole point of #12.

### Data flow (after)
```
ControlDef ──► rules/resolve.py::resolve_test_code(control)
                 inline → rule → file → ""
        ┌────────────────┴───────────────────┐
bundle/assemble.py                     store/run_service.py
_build_control_block                   (thin wrapper: short-circuits to None
  test_code = resolve_test_code(...)     for file-based store controls so
  → manifest "test_code" + workpaper     Workpaper.assemble reads test_path)
```

### Files
- **Create:** `uticen_lite/rules/resolve.py`
- **Modify:** `uticen_lite/bundle/assemble.py`, `uticen_lite/store/run_service.py`

### Gates
After changes: `python -m pytest -q` (green + pristine), `python -m ruff check .` (watch for unused-import on `pathlib`/`rule_to_text`/`parse_rule_spec`), `python -m mypy uticen_lite` (the `TYPE_CHECKING` import of `ControlDef` keeps the annotation typed without a runtime edge).

## Bundle / contract impact
**UNCHANGED.** This is a pure code-movement refactor: the resolved `test_code` string is computed by byte-identical logic, the manifest shape is untouched, no `schema_version` bump, no `contract/bundle.schema.json` edit, no change to `assemble_bundle` / `bundle/archive.py` output. The bundle remains schema-valid because the only producer of the `test_code` field (`_build_control_block`) now calls a relocated function with the same body and same priority order. `tests/test_contract_export.py` and `tests/schema/test_bundle_schema.py` pass unchanged (they assert byte-identity of the schema file and validity of assembled manifests, neither of which this touches).

## Testing
TDD order: write/adjust the resolver unit tests first (they fail until `rules/resolve.py` exists), then refactor the two call sites, then confirm the existing producer tests stay green unchanged.

**New unit tests — create `tests/rules/test_resolve.py`** (`from uticen_lite.rules.resolve import resolve_test_code`, build `ControlDef` via `uticen_lite.model.control`):
- `test_inline_wins_over_rule_and_file` — control with `test_code="X"` AND `rule_spec={...}` AND `test_path=<file>` → returns `"X"` (locks the canonical inline-first priority; this is the case the two old sites disagreed on).
- `test_rule_renders_to_text_when_no_inline` — `test_code=None`, `rule_spec` set → returns `rule_to_text(parse_rule_spec(rule_spec))`; assert `"Flag a record when ALL" in result`.
- `test_file_read_when_no_inline_no_rule` — `test_code=None`, `rule_spec=None`, `test_path=<tmp .py file>` → returns the file's exact bytes-as-text (use `tmp_path`).
- `test_empty_string_when_nothing_set` — `test_code=None`, `rule_spec=None`, `test_path=""` → returns `""`.

**Existing producer tests that must stay green UNCHANGED (regression guard — do not edit them):**
- `tests/bundle/test_assemble.py`: `test_control_test_code_matches_file` (file content embedded), `test_rule_control_bundles_readable_test_code` (`"Flag a record when ALL" in block["test_code"]`), `test_no_test_path_key`, `test_test_code_content_is_present`, `test_workpaper_reflects_latest_run_not_oldest`.
- `tests/store/test_run_service.py`: `test_run_persists_and_renders` (rule control → renders workpaper with rule text). Optionally **add** `test_run_persists_inline_control_test_code` — seed an inline-python control (`test_kind="python", test_code="# inline"`) and assert the rendered workpaper/`.md` contains `# inline`, to pin the inline branch of the wrapper (currently only the rule branch is exercised here).
- `tests/test_contract_export.py`, `tests/schema/test_bundle_schema.py`: byte-identity / validity gates.

No new fixtures beyond `tmp_path`; reuse `ControlDef` construction patterns already in `tests/bundle/test_assemble.py`.

## Non-goals / out of scope
- No change to priority semantics, the bundle manifest, or `bundle.schema.json` (cardinal rule 0001).
- No move of `resolve_test_code` into `model/control.py` (would invert the dependency; explicitly rejected by the locked decision and import-graph evidence).
- Not addressing multi-source `test()` resolution, run history (#14), or any control_test_kind expansion — scope is the two duplicated resolvers only.
- Not adding a public re-export from `rules/__init__.py` (it is empty today); callers import the function by its full path.
- Not refactoring `Workpaper.assemble`'s own deferred disk-read behavior — only the wrapper feeding it changes.

## Risks & mitigations
- **Risk: subtle behavior change from reordering rule-vs-inline in run_service.** Mitigation: the byte-identity argument above (store controls are mutually exclusive + `test_path` always `""`), plus the new inline-control run_service test and the unchanged rule-control test pin both branches.
- **Risk: unused-import lint failures after moving imports** (`pathlib` in assemble, `rule_to_text`/`parse_rule_spec` in run_service). Mitigation: grep each file for remaining uses and remove only genuinely dead imports; run `ruff check .` as the gate.
- **Risk: import cycle introduced by the new module.** Mitigation: confirmed `rules/` is a leaf (depends only on stdlib via `spec.py`); `ControlDef` is imported under `TYPE_CHECKING` only, so no runtime `rules → model` edge. mypy validates the annotation.
- **Risk: the contract test catches an accidental schema/file edit.** Mitigation: this refactor touches no schema file; if `tests/test_contract_export.py` ever fails, it means an unintended edit crept in — treat as a stop signal, not a regenerate-and-move-on.

## Resolved open questions (2026-06-20)
- **`rules/__init__.py` re-export:** not added — callers use the full import path `from uticen_lite.rules.resolve import resolve_test_code`. Deferred as a non-goal.
