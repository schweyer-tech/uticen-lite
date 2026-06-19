from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

OPERATORS = frozenset({
    "eq", "ne", "gt", "ge", "lt", "le",
    "is_empty", "not_empty", "in", "not_in", "regex", "is_duplicate",
})
_LOGIC = frozenset({"all", "any"})


class RuleSpecError(ValueError):
    """A rule_spec is malformed."""


@dataclass(frozen=True)
class Condition:
    column: str
    op: str
    value: Any = None


@dataclass(frozen=True)
class RuleSpec:
    logic: str
    conditions: list[Condition] = field(default_factory=list)
    severity: str = "medium"
    description_template: str = ""
    item_key_column: str | None = None


def parse_rule_spec(raw: dict) -> RuleSpec:
    logic = raw.get("logic", "all")
    if logic not in _LOGIC:
        raise RuleSpecError(f"logic must be one of {sorted(_LOGIC)}, got {logic!r}")
    raw_conditions = raw.get("conditions", [])
    if not isinstance(raw_conditions, list):
        raise RuleSpecError("conditions must be a list")
    conditions = []
    for c in raw_conditions:
        op = c.get("op")
        if op not in OPERATORS:
            raise RuleSpecError(f"unknown operator {op!r}")
        if not c.get("column"):
            raise RuleSpecError("each condition needs a column")
        conditions.append(Condition(column=c["column"], op=op, value=c.get("value")))
    return RuleSpec(
        logic=logic,
        conditions=conditions,
        severity=raw.get("severity", "medium"),
        description_template=raw.get("description_template", ""),
        item_key_column=raw.get("item_key_column"),
    )


def referenced_columns(spec: RuleSpec) -> list[str]:
    seen: list[str] = []
    for c in spec.conditions:
        if c.column not in seen:
            seen.append(c.column)
    return seen
