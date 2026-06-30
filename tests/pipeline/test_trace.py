"""Unit tests for the single-record trace view-model (issue #29)."""

from __future__ import annotations

import pandas as pd

from uticen_lite.model.population import ColumnMeta, Population
from uticen_lite.pipeline.model import parse_pipeline
from uticen_lite.pipeline.trace import trace_record


def _pop(df: pd.DataFrame, key: str = "id", sid: str = "src") -> Population:
    cols = [ColumnMeta(original_name=c, display_name=c, is_key=(c == key)) for c in df.columns]
    return Population(df=df, columns=cols, source_id=sid)


def _simple_pipeline():
    # Import(src) → Test(amount > 100)
    return parse_pipeline(
        {
            "nodes": [
                {"id": "imp", "type": "import", "source_id": "src", "inputs": []},
                {
                    "id": "tst",
                    "type": "test",
                    "inputs": ["imp"],
                    "config": {
                        "logic": "all",
                        "conditions": [{"column": "amount", "op": "gt", "value": 100}],
                    },
                },
            ]
        }
    )


def test_flagged_record_shows_condition_true_and_flagged():
    df = pd.DataFrame({"id": ["A", "B"], "amount": [150, 50]})
    frames = {"imp": df, "tst": df[df["amount"] > 100]}
    res = trace_record(_simple_pipeline(), frames, "A", {"src": _pop(df)})
    assert res.found is True
    assert res.key_column == "id"
    assert res.tests[0].flagged is True
    cond = res.tests[0].conditions[0]
    assert cond.column == "amount" and cond.passed is True
    assert str(cond.actual) == "150"


def test_passing_record_not_flagged():
    df = pd.DataFrame({"id": ["A", "B"], "amount": [150, 50]})
    frames = {"imp": df, "tst": df[df["amount"] > 100]}
    res = trace_record(_simple_pipeline(), frames, "B", {"src": _pop(df)})
    assert res.tests[0].flagged is False
    assert res.tests[0].conditions[0].passed is False


def test_record_dropped_at_filter_is_reported_at_that_node():
    pipe = parse_pipeline(
        {
            "nodes": [
                {"id": "imp", "type": "import", "source_id": "src", "inputs": []},
                {
                    "id": "flt",
                    "type": "filter",
                    "inputs": ["imp"],
                    "config": {
                        "logic": "all",
                        "conditions": [{"column": "active", "op": "eq", "value": "Y"}],
                    },
                },
                {
                    "id": "tst",
                    "type": "test",
                    "inputs": ["flt"],
                    "config": {
                        "logic": "all",
                        "conditions": [{"column": "amount", "op": "gt", "value": 100}],
                    },
                },
            ]
        }
    )
    df = pd.DataFrame({"id": ["A", "B"], "active": ["N", "Y"], "amount": [150, 150]})
    filtered = df[df["active"] == "Y"]
    frames = {"imp": df, "flt": filtered, "tst": filtered[filtered["amount"] > 100]}
    res = trace_record(pipe, frames, "A", {"src": _pop(df)})
    flt_step = next(s for s in res.steps if s.id == "flt")
    assert flt_step.status == "dropped"
    assert res.tests[0].reached is False


def test_missing_key_reports_not_found():
    df = pd.DataFrame({"id": ["A"], "amount": [1]})
    frames = {"imp": df, "tst": df.iloc[0:0]}
    res = trace_record(_simple_pipeline(), frames, "ZZZ", {"src": _pop(df)})
    assert res.found is False
    assert "No record" in res.message


def test_non_unique_key_traces_first_and_counts():
    df = pd.DataFrame({"id": ["A", "A"], "amount": [150, 50]})
    frames = {"imp": df, "tst": df[df["amount"] > 100]}
    res = trace_record(_simple_pipeline(), frames, "A", {"src": _pop(df)})
    assert res.found is True
    assert res.shared_count == 2


def test_exists_in_condition_uses_other_source():
    main = pd.DataFrame({"id": ["A", "B"], "vendor": ["v1", "v9"]})
    other = pd.DataFrame({"vendor_id": ["v1"]})
    pipe = parse_pipeline(
        {
            "nodes": [
                {"id": "imp", "type": "import", "source_id": "src", "inputs": []},
                {
                    "id": "tst",
                    "type": "test",
                    "inputs": ["imp"],
                    "config": {
                        "logic": "all",
                        "conditions": [
                            {
                                "op": "exists_in",
                                "other_source": "vendors",
                                "this_key": "vendor",
                                "other_key": "vendor_id",
                            }
                        ],
                    },
                },
            ]
        }
    )
    out = main[main["vendor"].isin({"v1"})]
    frames = {"imp": main, "tst": out}
    sources = {
        "src": _pop(main, key="id"),
        "vendors": _pop(other, key="vendor_id", sid="vendors"),
    }
    res = trace_record(pipe, frames, "A", sources)
    cond = res.tests[0].conditions[0]
    assert cond.passed is True
    assert "found in vendors" in cond.note


def test_custom_python_terminal_has_no_condition_detail():
    pipe = parse_pipeline(
        {
            "nodes": [
                {"id": "imp", "type": "import", "source_id": "src", "inputs": []},
                {
                    "id": "cpy",
                    "type": "custom_python",
                    "inputs": ["imp"],
                    "config": {"flavor": "test", "code": "rows = rows"},
                },
            ]
        }
    )
    df = pd.DataFrame({"id": ["A"], "amount": [1]})
    frames = {"imp": df, "cpy": pd.DataFrame({"item_key": ["A"]})}
    res = trace_record(pipe, frames, "A", {"src": _pop(df)})
    assert res.tests[0].conditions == []
    assert "custom Python" in res.tests[0].note


def test_source_without_key_column_degrades():
    df = pd.DataFrame({"amount": [1]})
    pop = Population(df=df, columns=[ColumnMeta("amount", "amount")], source_id="src")
    frames = {"imp": df, "tst": df.iloc[0:0]}
    res = trace_record(_simple_pipeline(), frames, "A", {"src": pop})
    assert res.key_column is None
    assert "no key column" in res.message.lower()


def test_condition_on_missing_column_does_not_crash():
    df = pd.DataFrame({"id": ["A"], "amount": [150]})
    pipe = parse_pipeline(
        {
            "nodes": [
                {"id": "imp", "type": "import", "source_id": "src", "inputs": []},
                {
                    "id": "tst",
                    "type": "test",
                    "inputs": ["imp"],
                    "config": {
                        "logic": "all",
                        "conditions": [{"column": "nope", "op": "gt", "value": 1}],
                    },
                },
            ]
        }
    )
    frames = {"imp": df, "tst": df.iloc[0:0]}
    res = trace_record(pipe, frames, "A", {"src": _pop(df)})
    # The bad condition is reported as un-evaluatable, not a crash.
    assert res.tests[0].conditions[0].passed is None
