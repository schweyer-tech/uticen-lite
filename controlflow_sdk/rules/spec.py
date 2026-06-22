from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

OPERATORS = frozenset({
    "eq", "ne", "gt", "ge", "lt", "le",
    "is_empty", "not_empty", "in", "not_in", "regex", "is_duplicate",
    "exists_in", "not_exists_in",
})
# Cross-source presence operators: a key column joined against another source.
_CROSS_SOURCE_OPS = frozenset({"exists_in", "not_exists_in"})
_LOGIC = frozenset({"all", "any"})


class RuleSpecError(ValueError):
    """A rule_spec is malformed."""


@dataclass(frozen=True)
class Condition:
    column: str
    op: str
    value: Any = None
    # Cross-source presence fields (exists_in / not_exists_in). ``None`` for all
    # single-source conditions, so existing specs are unchanged.
    other_source: str | None = None  # id of source B
    this_key: str | None = None      # key column in the primary population (A)
    other_key: str | None = None     # key column in source B


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
        if op in _CROSS_SOURCE_OPS:
            conditions.append(_parse_cross_source(c, op))
            continue
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


def _parse_cross_source(c: dict, op: str) -> Condition:
    """Validate and build a cross-source ``exists_in`` / ``not_exists_in`` condition.

    Requires ``other_source``, ``this_key`` and ``other_key`` (single key column
    only — composite keys are a non-goal). The condition's ``column`` is set to
    ``this_key`` so ``referenced_columns`` surfaces the join key for ``details``.
    """
    other_source = c.get("other_source")
    this_key = c.get("this_key")
    other_key = c.get("other_key")
    for name, val in (("other_source", other_source), ("this_key", this_key),
                      ("other_key", other_key)):
        if not val:
            raise RuleSpecError(f"{op!r} requires a {name}")
    if isinstance(this_key, list) or isinstance(other_key, list):
        raise RuleSpecError(f"{op!r} supports a single key column")
    return Condition(column=str(this_key), op=op, other_source=str(other_source),
                     this_key=str(this_key), other_key=str(other_key))


def referenced_columns(spec: RuleSpec) -> list[str]:
    seen: list[str] = []
    for c in spec.conditions:
        if c.column not in seen:
            seen.append(c.column)
    return seen
