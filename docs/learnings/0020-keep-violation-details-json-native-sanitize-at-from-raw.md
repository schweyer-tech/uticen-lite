---
id: 0020
date: 2026-06-22
area: data-integrity
tags: [rules, pipeline, violations, json, dtype, serialization, excel, pandas]
status: active
supersedes: null
superseded_by: null
---

# Keep violation `details` JSON-native — sanitize once at `Violation.from_raw`, because no-code conditions on typed columns leak pandas/numpy scalars

## Context

Converting the Northwind `privileged-access-review` control from a hand-written `test.py` to a no-code
pipeline (Filter `is_privileged` → Test `any`-of [no approver, stale review]) made `cflow run` fail with
`Object of type Timestamp is not JSON serializable`. The compiled Test builds each violation's `details`
from `row.to_dict()` over the **referenced condition columns** — and one of those columns, `last_review_date`,
loads as `datetime64[ns]` (per its `date` data_type, see [[0011]]). So `details` carried a pandas
`Timestamp`, which `json.dumps` cannot serialize when the run is persisted to the store / runlog / workpaper.

No existing no-code control had ever referenced a `date`/`number`/`boolean` column in a condition (the
flagship `mfa-enforcement` rule references booleans but passes clean, so its `details` were never built),
so the gap was latent until a builder rule put a non-text typed column into `details`.

## What went wrong / what worked

The hand-written Python controls dodged this by coercing every detail value explicitly (`str(...)`,
`float(...)`) before returning it. The no-code path can't — it emits a generic `{c: row[c]}` over the
referenced columns, so whatever dtype the column loaded as lands in `details` verbatim. The fix is to
sanitize at the **single funnel** both the rule path and the Python path pass through —
`Violation.from_raw` (`runner/execute.py` coerces every raw violation through it; `store/export_service.py`
too) — rather than scattering `default=str` across the 3+ `json.dumps` sinks (`run_service`, `repo`,
`runlog`) and the HTML renderer that reads `v.details` directly.

## The rule

A `Violation`'s `details` must hold only JSON-native values. Coerce them **once**, at `Violation.from_raw`,
with a duck-typed `_json_safe` (so the `model/` layer keeps its pandas/numpy-free imports): pandas/`datetime`
`Timestamp`/`date` → `isoformat()`; `NaT`/`NaN` (and any "not-equal-to-itself" sentinel) → `None` (check
this **before** `isoformat`, since `NaT.isoformat()` returns the string `"NaT"`); numpy scalars → `.item()`;
recurse into dict/list. Any new authoring surface, rule operator, or column type inherits the guarantee for
free because everything funnels through `from_raw`. Do **not** rely on a column happening to be text — that
is the same silently-fragile assumption as [[0011]]. This is a render/store concern only; `details` never
enter the bundle ([[0001]] trust boundary), so `schema_version` is untouched.

## Corollary — column-wise coercion at a serialization boundary (e.g. Excel) has two extra traps

The same "coerce pandas scalars to native at the serialization boundary" rule applies to the `.xlsx` step
exports (`adapters/xlsx_export.py::_coerce_for_excel`). But `_json_safe` operates on already-extracted
**scalars** (from `row.to_dict()`); coercing a whole **DataFrame column** hits two traps `_json_safe` never did:

1. **`isinstance(pd.NaT, pd.Timestamp)` is `False`** (and `pd.NA` is not a `Timestamp` either). A
   `Timestamp`-branch alone lets `NaT`/`NA` fall through unchanged. Guard them with an explicit identity check
   `v is pd.NaT or v is pd.NA → None` **before** the `Timestamp`/`isnull` branches.
2. **`Series.map(fn)` on a `datetime64` column re-casts a returned Python `None` back to `NaT`** (pandas
   re-infers the result dtype). To force real Python `None` regardless of the source column dtype, build an
   **object-dtype** Series via a list comprehension — `pd.Series([fn(v) for v in col], dtype=object,
   index=col.index)` — never `col.map(fn)`. (Also never `DataFrame.applymap` — deprecated, trips the
   pristine-output gate.)

Keep native numbers/dates (openpyxl writes by Python value type, so a `datetime`/`int`/`float` in an
object-dtype column still writes as an Excel date/number); stringify only list/dict/set. These exports are
local evidence and never touch the bundle ([[0001]]).

## Reference

- `controlflow_sdk/model/violation.py` (`_json_safe` + `Violation.from_raw` — the single sanitization point).
- `controlflow_sdk/adapters/xlsx_export.py` (`_coerce_for_excel` — column-wise coercion; the NaT-isinstance +
  `Series.map` re-cast traps above).
- `controlflow_sdk/runner/execute.py` (every raw violation is coerced via `Violation.from_raw`).
- `controlflow_sdk/pipeline/compile.py` / `controlflow_sdk/rules/render_rule.py` (emit `details` from the
  referenced condition columns — the source of the typed scalars).
- `controlflow_sdk/adapters/files.py::coerce_series` (loads `date`→`datetime64`, `boolean`→bool, `number`→float).
- `tests/model/test_violation.py::test_from_raw_makes_details_json_safe`.
- Related dtype trap: [[0011]]; cardinal trust boundary: [[0001]].
