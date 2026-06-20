from __future__ import annotations

from typing import Any

import pandas as pd

from controlflow_sdk.model.population import Population
from controlflow_sdk.rules.spec import Condition, RuleSpec, referenced_columns


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _safe_format(template: str, row: dict) -> str:
    if not template:
        return ""
    return template.format_map(_SafeDict(row))


def _condition_mask(
    df: pd.DataFrame, cond: Condition,
    sources: dict[str, Population] | None = None,
) -> pd.Series:
    op = cond.op
    if op in ("exists_in", "not_exists_in"):
        other = (sources or {}).get(cond.other_source or "")
        if other is None:
            raise ValueError(f"exists_in references unknown source {cond.other_source!r}")
        other_values = set(other.df[cond.other_key].dropna().astype(str))
        present = df[cond.this_key].astype(str).isin(other_values)
        return present if op == "exists_in" else ~present
    col = df[cond.column]
    value = cond.value
    if op == "eq":
        return col == value
    if op == "ne":
        return col != value
    if op == "gt":
        return col > value
    if op == "ge":
        return col >= value
    if op == "lt":
        return col < value
    if op == "le":
        return col <= value
    if op == "is_empty":
        return col.isna() | (col.astype(str) == "")
    if op == "not_empty":
        return ~(col.isna() | (col.astype(str) == ""))
    if op == "in":
        return col.isin(value or [])
    if op == "not_in":
        return ~col.isin(value or [])
    if op == "regex":
        return col.astype(str).str.match(str(value)).fillna(False)
    if op == "is_duplicate":
        return col.duplicated(keep=False)
    raise ValueError(f"unhandled operator {op!r}")  # pragma: no cover (validated upstream)


def evaluate_rule(
    spec: RuleSpec, pop: Population,
    sources: dict[str, Population] | None = None,
) -> list[dict]:
    df = pop.df
    if not spec.conditions:
        return []
    masks = [_condition_mask(df, c, sources) for c in spec.conditions]
    combined = masks[0]
    for m in masks[1:]:
        combined = (combined & m) if spec.logic == "all" else (combined | m)

    key_col = spec.item_key_column
    if not key_col:
        key_col = pop.key_columns[0] if pop.key_columns else None
    ref_cols = referenced_columns(spec)

    violations: list[dict] = []
    for idx, row in df[combined].iterrows():
        row_map = row.to_dict()
        item_key = str(row_map[key_col]) if key_col else str(idx)
        details: dict[str, Any] = {c: row_map[c] for c in ref_cols if c in row_map}
        violations.append({
            "item_key": item_key,
            "description": _safe_format(spec.description_template, row_map),
            "severity": spec.severity,
            "details": details,
        })
    return violations
