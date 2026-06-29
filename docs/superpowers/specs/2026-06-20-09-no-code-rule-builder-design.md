# Spec: No-code rule builder usable for non-developers (issue #9)
**Issue:** #9 · **Date:** 2026-06-20 · **Status:** approved-design

## Problem (1–3 sentences)
The no-code rule builder is not usable by a GRC analyst: condition columns are free-text (a typo silently yields zero violations with no feedback), there is no data-mapping/coercion validation (bad type coercions surface only at run time as wrong results), and the builder is single-source only so every cross-source control (terminated-access, 3-way match, vendor SoD) drops to the Python escape hatch. This spec makes condition columns a server-rendered dropdown of the bound source's columns, adds a data-preview + coercion-health check on the source Data tab, and adds ONE guided cross-source primitive ("key exists / not-exists in source B") that renders to plain-Python `test_code` so the bundle shape is untouched.

## Locked decisions
- **Column dropdown:** server-render `<select>` options from the bound source's columns (`repo.get_source`), with a free-text fallback for power users. Wire into `templates/partials/rule_condition.html` and the `condition_row` route.
- **Data preview + coercion check:** on the source Data tab (the GET sub-routes from learning 0007 already exist), show first N parsed rows and flag columns that coerced to all-empty/NaN/NaT for a non-text declared type.
- **Cross-source primitive:** add a guided "key exists in B / not-exists in B" condition type. Extend the rule grammar (`rules/spec.py`), the evaluator (`rules/evaluate.py`), and the renderer (`rules/render_rule.py`). CRITICAL: keep the bundle shape UNCHANGED — render the cross-source rule to plain Python via the EXISTING multi-source `test()` API so `test_code` stays plain Python text; do NOT add fields to `bundle.schema.json`. Grammar: a condition with op `exists_in`/`not_exists_in` carrying `{source, this_key, other_key}`. Single-key join only; composite keys are a non-goal.
- Honor cardinal rule 0001 (bundle is the contract); learnings 0005 (route colors through `var(--token)`, support `[data-theme=light]`), 0007 (server-rendered GET sub-route tabs, no client JS tabs), 0004 (audit positional consumers when ordering changes).

## Design

### Part A — Column dropdown in the condition builder

The first bound source is the rule's primary population (matches `runner/execute.py` which uses `populations[0]`). We pass that source's columns into both the initial render and the HTMX "add condition" partial so the column field is a `<select>` plus a free-text fallback.

**`uticen_lite/plane/routes/controls.py`**
- Add a helper `def _primary_columns(conn, source_ids: list[str]) -> list[dict]:` returning `repo.get_source(conn, source_ids[0])["columns"]` (the list of `{original_name, display_name, ...}`) or `[]` when no source is bound / source missing. Used to populate dropdowns.
- `new_control` / `edit_control`: add `"columns": _primary_columns(conn, <source_ids>)` to the template context. For `new_control` there is no bound source yet, so pass `[]` (free-text fallback shows). For `edit_control` use `control["source_ids"]`.
- `condition_row` route — change signature to accept an optional source id and resolve columns:
  ```python
  @app.get("/controls/_condition_row", response_class=HTMLResponse)
  def condition_row(request: Request, source_id: str = "",
                    conn: sqlite3.Connection = Depends(get_conn)) -> Any:
      cols = _primary_columns(conn, [source_id]) if source_id else []
      return templates.TemplateResponse(
          request, "partials/rule_condition.html", {"columns": cols})
  ```
  (Per learning 0002 this is a sync GET — keep `Depends(get_conn)`.)
- `_rule_spec_from_form` — extend to read the new cross-source fields and emit the new condition kind (see Part C). The `cond_column` field is still read the same way (the dropdown and the fallback text input both post `cond_column`), so existing parsing is preserved; a dropdown set to the empty sentinel `""` plus a non-empty `cond_column_freetext` lets power users override (see template below).

**`uticen_lite/plane/templates/partials/rule_condition.html`** — replace the free-text column input with a select + fallback, driven by a `columns` context var. The select posts `cond_column`; an "Other…" option reveals a sibling text input named `cond_column_freetext`. When `columns` is empty (e.g. brand-new control with no source yet), render only the free-text input named `cond_column` (current behavior) so nothing breaks:
```html
<div class="condition-row" data-condition>
  {% if columns %}
  <select name="cond_column" data-col-select>
    <option value="">— column —</option>
    {% for c in columns %}
    <option value="{{ c.original_name }}">{{ c.display_name }}{% if c.display_name != c.original_name %} ({{ c.original_name }}){% endif %}</option>
    {% endfor %}
    <option value="__other__">Other (type a name)…</option>
  </select>
  <input type="text" name="cond_column_freetext" placeholder="Custom column" data-col-freetext style="display:none;">
  {% else %}
  <input type="text" name="cond_column" placeholder="Column name">
  {% endif %}
  <select name="cond_op" data-op-select> … existing ops … </select>
  <input type="text" name="cond_value" placeholder="Value" data-val>
  {# cross-source fields, hidden unless op is exists_in/not_exists_in — Part C #}
</div>
```
- `_rule_spec_from_form` resolves the column: `col = freetext if (sel == "__other__" and freetext) else sel`. Concretely: read `form.getlist("cond_column")` and `form.getlist("cond_column_freetext")` zipped; when the select value is `"__other__"` use the freetext sibling. Keep stripping/whitespace-skip behavior (preserves `test_rule_spec_whitespace_column_skipped`).

**`uticen_lite/plane/templates/partials/rule_builder.html`** — the pre-existing condition rows (edit path) also need the select. Two changes:
1. Replace the inline free-text column input in the `{% for cond in ... %}` loop with the same select markup, marking the matching option `selected` (`{% if c.original_name == cond.column %}selected{% endif %}`); if `cond.column` is not among `columns`, select `__other__` and prefill `cond_column_freetext` with `cond.column`.
2. The "+ Add condition" button must pass the bound source to the partial route so new rows get the right dropdown. Since the source checkboxes can change after page load, pass the primary source via an `hx-vals` that reads the first checked `source_ids` box. Simplest robust approach: server-render the button's `hx-get` with the current primary id and additionally include `hx-include="[name='source_ids']"` is not enough (route needs a single id) — instead use a tiny JS shim already permitted (the page already ships inline JS for panes/CodeMirror). Add to the existing `<script>` in `control_edit.html`: on add-condition click, set the button's `hx-get` query `?source_id=<first checked source_ids>` before HTMX issues the request (via `htmx:configRequest` listener adding `evt.detail.parameters` or rewriting `hx-get`). Document this in the plan as the one allowed JS touch; it does not introduce client-side tabs (0007 unaffected — that rule is about tabs).

   Minimal listener:
   ```js
   document.body.addEventListener('htmx:configRequest', function (e) {
     if (e.detail.elt.matches('[hx-get*="_condition_row"]')) {
       var first = document.querySelector('input[name="source_ids"]:checked');
       if (first) e.detail.parameters['source_id'] = first.value;
     }
   });
   ```

### Part B — Data preview + coercion-health check on the Data tab

The Data tab route already parses the current file's rows. Add a per-column coercion-health summary using the SAME coercion the runner uses (`adapters/files.coerce_series`), so the preview matches run-time reality.

**New module `uticen_lite/plane/coercion_check.py`** (keeps `sources.py` lean; pandas import is fine here — the plane is CPython-only):
```python
from __future__ import annotations
import pandas as pd
from uticen_lite.adapters.files import coerce_series

def coercion_report(header: list[str], data_rows: list[list[str]],
                    columns: list[dict]) -> list[dict]:
    """For each declared column, coerce the parsed string series to its
    declared data_type and flag total coercion failure for non-text types.

    Returns rows: {original_name, display_name, data_type, total, bad, all_bad}
    where `bad` = count of values that became NaN/NaT (number/date) and the
    source value was non-empty; `all_bad` = True when every non-empty source
    value failed to coerce (the silent-wrong-result smell). `boolean` and
    `text` never coerce to empty, so they are reported with bad=0/all_bad=False
    (informational only — no false alarms).
    """
```
Implementation notes:
- Build a `pd.Series(dtype=str)` per column from the parsed `data_rows` by column index (align to `header`); treat `""`/whitespace as "source-empty" and exclude those from the denominator so a legitimately empty column does not raise a false alarm.
- For `number`: `coerced = coerce_series(series, "number")`; `bad = (coerced.isna() & non_empty_mask).sum()`.
- For `date`: same with `coerce_series(series, "date")` and `.isna()` (catches `NaT`).
- For `text`/`boolean`: `bad = 0`, `all_bad = False` (locked decision flags only non-text declared types that coerced to all-empty/NaN/NaT).
- `all_bad = non_empty_count > 0 and bad == non_empty_count`.
- Use only the displayed page's rows? No — compute over the FULL current file for an accurate verdict (the route already reads the whole file into `all_rows`; pass `data_rows` = all rows, not the paginated slice). This keeps the verdict honest even when page 1 happens to be clean.

**`uticen_lite/plane/routes/sources.py` — `source_data` handler:**
- After building `all_rows`, compute `report = coercion_report(header, data_rows, repo.get_source(conn, source_id)["columns"])` (using the full `data_rows`, not the paginated `rows`).
- Add `"coercion": report` to the template context. Keep paginated `rows` for display unchanged (0004: the displayed slice ordering is untouched; the report is computed over the full unsliced list, so no positional consumer is reordered).

**`uticen_lite/plane/templates/source_data.html`** — add a "Mapping check" card ABOVE the preview table, only when `coercion` has any flagged column:
```html
{% set flagged = coercion | selectattr('all_bad') | list %}
{% if flagged %}
<div class="card callout-warn">
  <h2>Mapping check</h2>
  <p class="hint">These columns are declared as a non-text type but no value parsed. Fix the data type on the
     <a href="/sources/{{ source.id }}">Definition</a> tab, or correct the data.</p>
  <ul>
    {% for col in flagged %}
    <li><span class="mono">{{ col.display_name }}</span> — declared <strong>{{ col.data_type }}</strong>,
        but all {{ col.total }} non-empty value(s) failed to parse.</li>
    {% endfor %}
  </ul>
</div>
{% endif %}
```
Style: route all colors through existing tokens (0005). Add a `.callout-warn` rule to `uticen_lite/plane/static/app.css` using `var(--accent-muted)`/`var(--border-default)` and a warning foreground token already present (e.g. reuse the severity/high token if defined; otherwise add `--callout-warn-*` tokens for BOTH default and `[data-theme=light]`). No new stylesheet; one design language (0005).

### Part C — Cross-source "exists_in / not_exists_in" primitive

#### Grammar (`uticen_lite/rules/spec.py`)
- Add the two ops: `OPERATORS = frozenset({... , "exists_in", "not_exists_in"})`.
- Extend `Condition` with cross-source fields (default `None` so existing single-source conditions are unchanged and remain frozen-dataclass-compatible):
  ```python
  @dataclass(frozen=True)
  class Condition:
      column: str
      op: str
      value: Any = None
      other_source: str | None = None   # id of source B
      this_key: str | None = None       # key column in primary (A)
      other_key: str | None = None      # key column in B
  ```
- `parse_rule_spec`: when `op in {"exists_in","not_exists_in"}`, require `other_source` and `this_key` and `other_key` (raise `RuleSpecError` with a clear message if any missing); the `column` field for these ops is set to `this_key` (so `referenced_columns` still surfaces the key and `_condition_mask` is never reached for these ops). Single-key only — if a future caller passes a list, raise `RuleSpecError("exists_in supports a single key column")` (composite is a non-goal).
- `referenced_columns` unchanged (still returns distinct `c.column`; for cross-source conditions that is the primary key column — correct for `details`).

#### Evaluator (`uticen_lite/rules/evaluate.py`)
The runner currently calls `evaluate_rule(spec, primary)` with a single Population. Cross-source needs source B. Extend the signature with an optional sources map (backward compatible — all existing callers/tests pass one positional arg):
```python
def evaluate_rule(spec: RuleSpec, pop: Population,
                  sources: dict[str, Population] | None = None) -> list[dict]:
```
- In `_condition_mask`, branch on the new ops BEFORE indexing `df[cond.column]`:
  ```python
  if op in ("exists_in", "not_exists_in"):
      other = (sources or {}).get(cond.other_source)
      if other is None:
          raise ValueError(f"exists_in references unknown source {cond.other_source!r}")
      other_values = set(other.df[cond.other_key].dropna().astype(str))
      present = df[cond.this_key].astype(str).isin(other_values)
      return present if op == "exists_in" else ~present
  ```
  `_condition_mask` must therefore accept `sources` (thread it through from `evaluate_rule`). Stringify both sides (matches how item keys are stringified elsewhere) so a numeric/text mismatch across files still joins. Single-key only.
- Everything downstream (mask combine, item_key, details, description_template) is unchanged.

#### Runner wiring (`uticen_lite/runner/execute.py`)
`run_control` already builds `sources_by_id: dict[str, Population]`. Change the rule branch to pass it:
```python
raw_result = evaluate_rule(parse_rule_spec(control.rule_spec), primary, sources_by_id)
```
No other runner change. The primary is still `populations[0]` (first bound source); source B must be among `control.sources`, so the builder must bind BOTH sources to the control (enforced in the form — see UI below).

#### Renderer → plain Python `test_code` (`uticen_lite/rules/render_rule.py`)
This is the contract-critical piece. The bundle's `test_code` is a plain string (schema unchanged). For a spec that contains NO cross-source condition, `rule_to_text` keeps emitting the existing human-readable text (unchanged — preserves `test_render_rule` tests and existing bundle output). For a spec that contains at least one cross-source condition, `rule_to_text` MUST emit runnable multi-source Python using the existing `test(pop, sources)` API, because the human-readable form cannot express the join and the app/SDK both execute `test_code` as Python:

```python
def rule_to_text(spec: RuleSpec) -> str:
    if any(c.op in ("exists_in", "not_exists_in") for c in spec.conditions):
        return _render_python(spec)
    # ... existing human-readable rendering ...
```
`_render_python(spec)` generates a deterministic, self-contained function, e.g. for a terminated-access control (flag active-directory accounts whose user is NOT in the current-HR roster):
```python
def test(pop, sources):
    import pandas as pd
    df = pop.df
    other = sources["hr_roster"].df
    other_keys = set(other["employee_id"].dropna().astype(str))
    present = df["user_id"].astype(str).isin(other_keys)
    mask = ~present                      # not_exists_in
    # (additional single-source conditions AND/OR-combined here)
    out = []
    for _, row in df[mask].iterrows():
        r = row.to_dict()
        out.append({
            "item_key": str(r.get("user_id", "")),
            "description": ("...".format(**r)) if "<template>" else "",
            "severity": "high",
            "details": {k: r[k] for k in ["user_id"] if k in r},
        })
    return out
```
Generation rules:
- Combine single-source condition masks with the spec's `logic` (`&`/`|`) exactly as `evaluate_rule` does, then combine with each cross-source mask using the same logic operator, so the rendered Python is behaviorally identical to the live no-code evaluation. (Validate this equivalence with a test that runs BOTH paths on the same fixtures — see Testing.)
- Emit `item_key` from `spec.item_key_column` (fallback to the first key column / index), `severity` from `spec.severity`, `details` from `referenced_columns(spec)`, and `description` from `description_template` via `str.format` with a missing-key-safe shim (mirror `_SafeDict` — inline a small helper in the generated code or use `.format_map` with a local SafeDict class defined in the emitted source).
- Keep generation pure-Python and deterministic (sorted keys, stable column order) so bundle output is diffable (mirrors `assemble._sort_dict` discipline).

`bundle/assemble._resolve_test_code` and `store/run_service.run_control_in_store` already call `rule_to_text(parse_rule_spec(rule_spec))` for rule controls — so once `rule_to_text` emits Python for cross-source specs, BOTH the workpaper procedure `test_code` and the bundle `control.test_code` automatically carry runnable plain Python with zero changes to those modules. The schema field `control.test_code` (type string) is satisfied unchanged.

#### UI for the cross-source condition (`rule_condition.html` + `controls.py`)
- Add `exists_in` / `not_exists_in` to the `cond_op` `<select>` (in both `rule_condition.html` and the edit-loop block of `rule_builder.html`): labels "exists in another source" / "not in another source".
- Add three extra inputs in the condition row, shown only when the op is a cross-source op (toggled by the existing inline JS via a `data-xsrc` group; default `display:none`):
  - `cond_other_source` — a `<select>` of all OTHER bound sources (populate from a new `all_sources` context list = `repo.list_sources(conn)`; the template filters out the primary). Each option value = source id.
  - `cond_this_key` — a `<select>` of the PRIMARY source's columns (reuse `columns`).
  - `cond_other_key` — free-text (cannot pre-resolve B's columns without a second HTMX round-trip; free-text is acceptable for the first cut and keeps scope contained; note an enhancement to fetch B's columns via HTMX as a non-goal).
- These post as parallel `getlist` fields aligned with the other `cond_*` lists. `_rule_spec_from_form` builds the condition:
  ```python
  if op in ("exists_in", "not_exists_in"):
      cond = {"op": op, "column": this_key, "other_source": other_source,
              "this_key": this_key, "other_key": other_key}
  ```
  (No `value` for these ops — same skip pattern as unary ops.)
- The condition builder must ensure source B is bound to the control. Validate in `_save_from_form`: collect every `other_source` referenced by a cross-source condition and union them into the `source_ids` saved via `repo.set_control_sources` (so the runner loads B). This keeps the analyst from having to remember to also tick B's checkbox; document it in the UI hint.

### Exact files to create / modify
Create:
- `uticen_lite/plane/coercion_check.py`

Modify:
- `uticen_lite/rules/spec.py` (ops + Condition fields + parse validation)
- `uticen_lite/rules/evaluate.py` (sources param + exists_in/not_exists_in masks)
- `uticen_lite/rules/render_rule.py` (`_render_python` for cross-source specs)
- `uticen_lite/runner/execute.py` (pass `sources_by_id` into `evaluate_rule`)
- `uticen_lite/plane/routes/controls.py` (`_primary_columns`, `condition_row` source_id, context vars, `_rule_spec_from_form` + `_save_from_form` cross-source + freetext)
- `uticen_lite/plane/routes/sources.py` (`source_data` coercion report)
- `uticen_lite/plane/templates/partials/rule_condition.html`
- `uticen_lite/plane/templates/partials/rule_builder.html`
- `uticen_lite/plane/templates/control_edit.html` (inline JS: add `source_id` to condition_row request + xsrc field toggle)
- `uticen_lite/plane/templates/source_data.html` (Mapping-check card)
- `uticen_lite/plane/static/app.css` (`.callout-warn` tokens, light + dark)

## Bundle / contract impact
**Unchanged.** No edit to `contract/bundle.schema.json`, `bundle/assemble.py`, or `bundle/archive.py`. The cross-source rule is persisted only as a richer `rule_spec` (store-internal JSON, never in the bundle). At export, `_resolve_test_code` → `rule_to_text(parse_rule_spec(rule_spec))` now returns runnable plain Python for cross-source specs, which lands in the existing `control.test_code` string field (and the workpaper procedure's `test_code`) — both already string-typed in the schema. No new manifest keys, no raw population data, no filesystem paths. `schema_version` stays `"1.0"`. The gates `tests/test_contract_export.py` + `tests/schema/test_bundle_schema.py` must stay green unchanged (a new test asserts a cross-source control still produces a schema-valid bundle).

## Testing
TDD order — write the failing test first in each case.

Unit (`tests/rules/`):
- `tests/rules/test_spec.py`: `exists_in`/`not_exists_in` parse with `{other_source,this_key,other_key}`; missing any field raises `RuleSpecError`; `OPERATORS` now includes the two ops (update `test_operators_cover_v1_set`); a passed key list raises (composite is rejected).
- `tests/rules/test_evaluate.py`: build TWO populations (A = users, B = hr_roster) with 2+ rows each (0004); assert `not_exists_in` flags A-rows whose key is absent from B and `exists_in` flags those present; numeric-vs-string keys still join (stringified); referencing an unknown `other_source` raises `ValueError`.
- `tests/rules/test_render_rule.py`: single-source specs still render the existing human-readable text (regression — existing assertions unchanged); a cross-source spec renders a `def test(pop, sources):` body that references `sources["<B>"]` and the right keys; NEW equivalence test: `exec` the rendered Python and run it against the same two fixtures, asserting identical `item_key` lists to `evaluate_rule(spec, A, {B: ...})` (proves render ≡ evaluate).

Route / integration (`tests/plane/`):
- `tests/plane/test_rule_builder.py`: `condition_row?source_id=users` returns a `<select name="cond_column">` containing the source's columns + an "Other" option; `condition_row` with no source returns a free-text `cond_column` input (fallback). `_rule_spec_from_form` resolves `__other__` + `cond_column_freetext` to the typed column. `_rule_spec_from_form` builds an `exists_in` condition from the cross-source form fields; `_save_from_form` auto-binds source B into `control_sources`.
- `tests/plane/test_sources.py`: upload a CSV with a column declared `number` whose every value is non-numeric (e.g. `amount` = `"n/a"`), set the data type via `/sources/<id>` save, then GET `/sources/<id>/data` and assert the Mapping-check card text appears; upload a clean numeric column and assert NO card; an all-empty column declared `number` does NOT trigger the card (empty excluded from denominator).
- `tests/store/test_run_service.py` (or a new `test_run_service_cross_source` case): seed two sources + a `not_exists_in` rule control, `run_control_in_store`, assert the violation count matches the expected terminated-access result and the rendered workpaper HTML/`md` exist and the procedure `test_code` is the generated Python (contains `def test(pop, sources)`).
- Bundle regression (`tests/plane/test_export.py` and/or `tests/test_contract_export.py`): export an engagement containing a cross-source rule control; assert the bundle validates and `control.test_code` is non-empty runnable Python.

New fixtures: a two-source seed (primary `access` + lookup `hr_roster`) reused across evaluate/run/export tests; each with ≥2 rows including one present and one absent key (0004).

## Non-goals / out of scope
- Composite (multi-column) join keys for `exists_in`/`not_exists_in` — single key only this cut.
- Arbitrary joins / aggregations / 3-way reconciliation beyond presence/absence — remain in the Python escape hatch.
- HTMX-fetching source B's column list to make `cond_other_key` a dropdown — free-text for now.
- Any change to `bundle.schema.json` or `schema_version`.
- Auto-fixing a bad coercion (the Mapping-check card only flags + links to the Definition tab; it does not edit data types).
- Live preview of rule results (run-before-save) — separate concern.
- Validating that free-text `cond_column` actually exists in the source at save time (the dropdown is the affordance; free-text remains an unguarded power-user path by design).

## Risks & mitigations
- **Render-vs-evaluate drift:** the live no-code run uses `evaluate_rule`; the bundle/workpaper uses the generated Python. If they diverge, the app imports something that behaves differently than what the analyst tested. Mitigation: the equivalence test in `test_render_rule.py` execs the generated code and asserts identical violations to `evaluate_rule` on shared fixtures; combine masks with identical logic operators in both code paths.
- **Generated `test_code` safety/quality:** emit deterministic, dependency-free Python (only `pandas`, already available), sorted keys, a local SafeDict for templates; no f-string injection of untrusted values into code positions — column/source names go into string literals via `repr()`/JSON-quoting, never bare interpolation, to avoid breaking on names with quotes.
- **`condition_row` now needs a connection:** switching it to `Depends(get_conn)` is a sync GET (safe per 0002); do NOT open a per-handler connection there.
- **Coercion check cost on large files:** the Data route already reads the whole file; coercion uses vectorized `coerce_series` over the existing parsed rows, so it adds one pass, not a re-read. Acceptable for the localhost, brittle-by-design plane.
- **Adding select markup to the edit loop could regress existing single-source edit:** covered by keeping the free-text fallback when a stored `cond.column` is not in `columns` (selects `__other__` + prefills freetext), and by the existing `test_edit_control_shows_values` plus new assertions.
- **`Condition` gaining fields:** it is a frozen dataclass with defaulted new fields, so all existing construction sites and `parse_rule_spec` paths stay valid; `referenced_columns` unchanged.

## Resolved open questions (2026-06-20)
- **Mapping-check callout styling:** introduce dedicated `--callout-warn-*` design tokens (both `[data-theme]` variants) per learning 0005 — do NOT couple to severity styling.
- **`logic='any'` mixing single- + cross-source conditions:** supported. Keep the general design that combines per-condition masks with the spec's `logic`. No v1 restriction to `logic='all'`.
- **Cross-source `other_key` input:** free-text is the committed baseline for v1 (single-key join). If cheap, populating source B's key from the same column-fetch is a welcome nicety, but free-text is acceptable and is what the spec commits to.
