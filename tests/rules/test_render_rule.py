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


# ---------------------------------------------------------------------------
# Cross-source specs render to runnable plain Python (issue #9)
# ---------------------------------------------------------------------------

import pandas as pd  # noqa: E402

from controlflow_sdk.model.population import ColumnMeta, Population  # noqa: E402
from controlflow_sdk.rules.evaluate import evaluate_rule  # noqa: E402


def _pop(df, key="user_id"):
    cols = [ColumnMeta(original_name=c, display_name=c, is_key=(c == key))
            for c in df.columns]
    return Population(df=df, columns=cols, source_id="s")


def test_cross_source_spec_renders_python_test_function():
    spec = parse_rule_spec({"logic": "all", "conditions": [{
        "op": "not_exists_in", "column": "user_id", "other_source": "hr_roster",
        "this_key": "user_id", "other_key": "employee_id"}],
        "severity": "high", "item_key_column": "user_id"})
    code = rule_to_text(spec)
    assert "def test(pop, sources):" in code
    assert "sources['hr_roster']" in code  # repr()-quoted source name
    assert "employee_id" in code and "user_id" in code


def test_single_source_spec_still_renders_human_text():
    """Regression: single-source specs keep emitting human-readable text."""
    spec = parse_rule_spec({"logic": "all", "conditions": [
        {"column": "can_create", "op": "eq", "value": True}]})
    text = rule_to_text(spec)
    assert "Flag a record when ALL of the following are true:" in text
    assert "def test(" not in text


def test_rendered_python_equivalent_to_evaluate_rule():
    """The generated Python must produce identical item_keys to evaluate_rule."""
    a_df = pd.DataFrame({"user_id": ["U1", "U2", "U3", "U4"],
                         "dept": ["IT", "Sales", "IT", "IT"]})
    b_df = pd.DataFrame({"employee_id": ["U1", "U3"], "name": ["Ann", "Cara"]})
    a = _pop(a_df, key="user_id")
    b = _pop(b_df, key="employee_id")
    spec = parse_rule_spec({"logic": "all", "conditions": [
        {"op": "not_exists_in", "column": "user_id", "other_source": "hr_roster",
         "this_key": "user_id", "other_key": "employee_id"},
        {"column": "dept", "op": "eq", "value": "IT"}],
        "severity": "high",
        "description_template": "Terminated user {user_id} retains access",
        "item_key_column": "user_id"})

    expected = evaluate_rule(spec, a, {"hr_roster": b})

    code = rule_to_text(spec)
    ns: dict = {}
    exec(code, ns)  # noqa: S102 — exercising the generated test() body
    actual = ns["test"](a, {"hr_roster": b})

    assert [v["item_key"] for v in actual] == [v["item_key"] for v in expected]
    # full violation parity (description, severity, details)
    assert actual == expected
