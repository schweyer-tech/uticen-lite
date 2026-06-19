import pytest

from controlflow_sdk.rules.spec import (
    OPERATORS,
    RuleSpec,
    RuleSpecError,
    parse_rule_spec,
    referenced_columns,
)


def test_parse_minimal_rule():
    spec = parse_rule_spec({
        "logic": "all",
        "conditions": [{"column": "can_create", "op": "eq", "value": True}],
        "severity": "high",
        "description_template": "User {user_id} flagged",
        "item_key_column": "user_id",
    })
    assert isinstance(spec, RuleSpec)
    assert spec.logic == "all"
    assert spec.conditions[0].column == "can_create"
    assert spec.severity == "high"
    assert referenced_columns(spec) == ["can_create"]


def test_operators_cover_v1_set():
    assert OPERATORS == frozenset({
        "eq", "ne", "gt", "ge", "lt", "le",
        "is_empty", "not_empty", "in", "not_in", "regex", "is_duplicate",
    })


def test_unknown_operator_raises():
    with pytest.raises(RuleSpecError):
        parse_rule_spec({"logic": "all",
                         "conditions": [{"column": "x", "op": "between", "value": 1}]})


def test_bad_logic_raises():
    with pytest.raises(RuleSpecError):
        parse_rule_spec({"logic": "xor", "conditions": []})


def test_conditions_must_be_list():
    with pytest.raises(RuleSpecError):
        parse_rule_spec({"logic": "all", "conditions": {"column": "x"}})
