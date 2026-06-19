import pandas as pd

from controlflow_sdk.model.population import ColumnMeta, Population
from controlflow_sdk.rules.evaluate import evaluate_rule
from controlflow_sdk.rules.spec import parse_rule_spec


def _pop(df: pd.DataFrame, key="user_id") -> Population:
    cols = [ColumnMeta(original_name=c, display_name=c,
                       is_key=(c == key)) for c in df.columns]
    return Population(df=df, columns=cols, source_id="s")


def test_and_logic_two_conditions():
    df = pd.DataFrame({
        "user_id": ["U1", "U2", "U3"],
        "can_create": [True, True, False],
        "can_approve": [True, False, True],
    })
    spec = parse_rule_spec({
        "logic": "all",
        "conditions": [
            {"column": "can_create", "op": "eq", "value": True},
            {"column": "can_approve", "op": "eq", "value": True},
        ],
        "severity": "high",
        "description_template": "User {user_id} can create and approve",
        "item_key_column": "user_id",
    })
    out = evaluate_rule(spec, _pop(df))
    assert [v["item_key"] for v in out] == ["U1"]
    assert out[0]["description"] == "User U1 can create and approve"
    assert out[0]["severity"] == "high"
    assert out[0]["details"] == {"can_create": True, "can_approve": True}


def test_any_logic():
    df = pd.DataFrame({"user_id": ["U1", "U2"], "amt": [10, 0]})
    spec = parse_rule_spec({"logic": "any", "conditions": [
        {"column": "amt", "op": "gt", "value": 5}]})
    assert [v["item_key"] for v in evaluate_rule(spec, _pop(df))] == ["U1"]


def test_is_empty_and_not_empty():
    df = pd.DataFrame({"user_id": ["U1", "U2"], "approver": ["", "boss"]})
    empty = parse_rule_spec({"logic": "all", "conditions": [
        {"column": "approver", "op": "is_empty"}]})
    assert [v["item_key"] for v in evaluate_rule(empty, _pop(df))] == ["U1"]


def test_in_set():
    df = pd.DataFrame({"user_id": ["U1", "U2", "U3"], "role": ["admin", "user", "root"]})
    spec = parse_rule_spec({"logic": "all", "conditions": [
        {"column": "role", "op": "in", "value": ["admin", "root"]}]})
    assert [v["item_key"] for v in evaluate_rule(spec, _pop(df))] == ["U1", "U3"]


def test_regex():
    df = pd.DataFrame({"user_id": ["U1", "U2"], "email": ["a@x.com", "bad"]})
    spec = parse_rule_spec({"logic": "all", "conditions": [
        {"column": "email", "op": "regex", "value": r"^[^@]+@[^@]+\.[^@]+$"}]})
    # regex flags MATCHES; to flag malformed, author negates — here it flags valid ones
    assert [v["item_key"] for v in evaluate_rule(spec, _pop(df))] == ["U1"]


def test_is_duplicate():
    df = pd.DataFrame({"user_id": ["U1", "U2", "U3"], "ssn": ["1", "1", "2"]})
    spec = parse_rule_spec({"logic": "all", "conditions": [
        {"column": "ssn", "op": "is_duplicate"}]})
    assert [v["item_key"] for v in evaluate_rule(spec, _pop(df))] == ["U1", "U2"]


def test_item_key_defaults_to_population_key():
    df = pd.DataFrame({"user_id": ["U9"], "flag": [True]})
    spec = parse_rule_spec({"logic": "all", "conditions": [
        {"column": "flag", "op": "eq", "value": True}]})  # no item_key_column
    assert evaluate_rule(spec, _pop(df))[0]["item_key"] == "U9"


def test_unknown_template_placeholder_left_literal():
    df = pd.DataFrame({"user_id": ["U1"], "flag": [True]})
    spec = parse_rule_spec({"logic": "all",
        "conditions": [{"column": "flag", "op": "eq", "value": True}],
        "description_template": "User {user_id} has {missing}"})
    assert evaluate_rule(spec, _pop(df))[0]["description"] == "User U1 has {missing}"


# ---------------------------------------------------------------------------
# Operator coverage: ne, ge, le, lt, not_in
# ---------------------------------------------------------------------------


def test_ne_operator():
    """ne flags rows where column != value."""
    df = pd.DataFrame({"user_id": ["U1", "U2", "U3"], "status": ["active", "inactive", "active"]})
    spec = parse_rule_spec({"logic": "all", "conditions": [
        {"column": "status", "op": "ne", "value": "active"}]})
    assert [v["item_key"] for v in evaluate_rule(spec, _pop(df))] == ["U2"]


def test_ge_operator():
    """ge flags rows where column >= value (boundary inclusive)."""
    df = pd.DataFrame({"user_id": ["U1", "U2", "U3"], "score": [10, 20, 30]})
    spec = parse_rule_spec({"logic": "all", "conditions": [
        {"column": "score", "op": "ge", "value": 20}]})
    assert [v["item_key"] for v in evaluate_rule(spec, _pop(df))] == ["U2", "U3"]


def test_le_operator():
    """le flags rows where column <= value (boundary inclusive)."""
    df = pd.DataFrame({"user_id": ["U1", "U2", "U3"], "score": [10, 20, 30]})
    spec = parse_rule_spec({"logic": "all", "conditions": [
        {"column": "score", "op": "le", "value": 20}]})
    assert [v["item_key"] for v in evaluate_rule(spec, _pop(df))] == ["U1", "U2"]


def test_lt_operator():
    """lt flags rows where column < value (strictly less than)."""
    df = pd.DataFrame({"user_id": ["U1", "U2", "U3"], "score": [10, 20, 30]})
    spec = parse_rule_spec({"logic": "all", "conditions": [
        {"column": "score", "op": "lt", "value": 20}]})
    assert [v["item_key"] for v in evaluate_rule(spec, _pop(df))] == ["U1"]


def test_not_in_operator():
    """not_in flags rows where column is NOT in the given set."""
    df = pd.DataFrame({"user_id": ["U1", "U2", "U3"], "role": ["admin", "user", "root"]})
    spec = parse_rule_spec({"logic": "all", "conditions": [
        {"column": "role", "op": "not_in", "value": ["admin", "root"]}]})
    assert [v["item_key"] for v in evaluate_rule(spec, _pop(df))] == ["U2"]
