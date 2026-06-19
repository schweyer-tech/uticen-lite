from controlflow_sdk.rules.render_rule import rule_to_text
from controlflow_sdk.rules.spec import parse_rule_spec


def test_rule_to_text_reads_as_a_rule():
    spec = parse_rule_spec({
        "logic": "all",
        "conditions": [
            {"column": "can_create", "op": "eq", "value": True},
            {"column": "can_approve", "op": "eq", "value": True},
        ],
        "severity": "high",
    })
    text = rule_to_text(spec)
    assert "Flag a record when ALL of the following are true:" in text
    assert "can_create = True" in text
    assert "can_approve = True" in text
    assert "severity: high" in text


def test_any_logic_and_unary_op_render():
    spec = parse_rule_spec({"logic": "any", "conditions": [
        {"column": "approver", "op": "is_empty"},
        {"column": "ssn", "op": "is_duplicate"}]})
    text = rule_to_text(spec)
    assert "ANY of the following" in text
    assert "approver is empty" in text
    assert "ssn is duplicated" in text
