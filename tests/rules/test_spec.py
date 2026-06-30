import pytest

from uticen_lite.rules.spec import (
    OPERATORS,
    RuleSpec,
    RuleSpecError,
    parse_rule_spec,
    referenced_columns,
)


def test_parse_minimal_rule():
    spec = parse_rule_spec(
        {
            "logic": "all",
            "conditions": [{"column": "can_create", "op": "eq", "value": True}],
            "severity": "high",
            "description_template": "User {user_id} flagged",
            "item_key_column": "user_id",
        }
    )
    assert isinstance(spec, RuleSpec)
    assert spec.logic == "all"
    assert spec.conditions[0].column == "can_create"
    assert spec.severity == "high"
    assert referenced_columns(spec) == ["can_create"]


def test_operators_cover_v1_set():
    assert OPERATORS == frozenset(
        {
            "eq",
            "ne",
            "gt",
            "ge",
            "lt",
            "le",
            "is_empty",
            "not_empty",
            "in",
            "not_in",
            "regex",
            "is_duplicate",
            "exists_in",
            "not_exists_in",
        }
    )


def test_unknown_operator_raises():
    with pytest.raises(RuleSpecError):
        parse_rule_spec(
            {"logic": "all", "conditions": [{"column": "x", "op": "between", "value": 1}]}
        )


def test_bad_logic_raises():
    with pytest.raises(RuleSpecError):
        parse_rule_spec({"logic": "xor", "conditions": []})


def test_conditions_must_be_list():
    with pytest.raises(RuleSpecError):
        parse_rule_spec({"logic": "all", "conditions": {"column": "x"}})


# ---------------------------------------------------------------------------
# Cross-source exists_in / not_exists_in (issue #9)
# ---------------------------------------------------------------------------


def test_exists_in_parses_cross_source_fields():
    spec = parse_rule_spec(
        {
            "logic": "all",
            "conditions": [
                {
                    "op": "exists_in",
                    "column": "user_id",
                    "other_source": "hr_roster",
                    "this_key": "user_id",
                    "other_key": "employee_id",
                }
            ],
        }
    )
    cond = spec.conditions[0]
    assert cond.op == "exists_in"
    assert cond.other_source == "hr_roster"
    assert cond.this_key == "user_id"
    assert cond.other_key == "employee_id"
    # the primary key surfaces in referenced_columns for details
    assert referenced_columns(spec) == ["user_id"]


def test_not_exists_in_parses_cross_source_fields():
    spec = parse_rule_spec(
        {
            "logic": "all",
            "conditions": [
                {
                    "op": "not_exists_in",
                    "column": "user_id",
                    "other_source": "hr_roster",
                    "this_key": "user_id",
                    "other_key": "employee_id",
                }
            ],
        }
    )
    assert spec.conditions[0].op == "not_exists_in"
    assert spec.conditions[0].other_key == "employee_id"


def test_exists_in_missing_other_source_raises():
    with pytest.raises(RuleSpecError):
        parse_rule_spec(
            {
                "logic": "all",
                "conditions": [
                    {"op": "exists_in", "this_key": "user_id", "other_key": "employee_id"}
                ],
            }
        )


def test_exists_in_missing_this_key_raises():
    with pytest.raises(RuleSpecError):
        parse_rule_spec(
            {
                "logic": "all",
                "conditions": [
                    {"op": "exists_in", "other_source": "hr", "other_key": "employee_id"}
                ],
            }
        )


def test_exists_in_missing_other_key_raises():
    with pytest.raises(RuleSpecError):
        parse_rule_spec(
            {
                "logic": "all",
                "conditions": [{"op": "exists_in", "other_source": "hr", "this_key": "user_id"}],
            }
        )


def test_exists_in_composite_key_rejected():
    with pytest.raises(RuleSpecError):
        parse_rule_spec(
            {
                "logic": "all",
                "conditions": [
                    {
                        "op": "exists_in",
                        "other_source": "hr",
                        "this_key": ["a", "b"],
                        "other_key": "employee_id",
                    }
                ],
            }
        )
