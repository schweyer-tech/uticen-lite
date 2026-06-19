from __future__ import annotations

from controlflow_sdk.rules.spec import Condition, RuleSpec

_BINARY = {
    "eq": "=", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<=",
}
_SET = {"in": "in", "not_in": "not in"}
_UNARY = {"is_empty": "is empty", "not_empty": "is not empty",
          "is_duplicate": "is duplicated"}


def _condition_text(c: Condition) -> str:
    if c.op in _BINARY:
        return f"{c.column} {_BINARY[c.op]} {c.value}"
    if c.op in _SET:
        return f"{c.column} {_SET[c.op]} {c.value}"
    if c.op == "regex":
        return f"{c.column} matches /{c.value}/"
    if c.op in _UNARY:
        return f"{c.column} {_UNARY[c.op]}"
    return f"{c.column} {c.op} {c.value}"  # pragma: no cover


def rule_to_text(spec: RuleSpec) -> str:
    joiner = "ALL" if spec.logic == "all" else "ANY"
    lines = [f"Flag a record when {joiner} of the following are true:"]
    lines += [f"  - {_condition_text(c)}" for c in spec.conditions]
    lines.append(f"severity: {spec.severity}")
    return "\n".join(lines)
