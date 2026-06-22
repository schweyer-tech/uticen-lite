"""Live row-counts at every joint (issue #25, Stage 3, spec §4/§5).

The count surviving each node is the offline feedback loop a non-developer uses
to see a mistake. These tests assert the counter matches the compiler's exact
node semantics over loaded sample frames (the terminated-access exemplar plus a
custom-python transform).
"""

from __future__ import annotations

import pandas as pd

from controlflow_sdk.pipeline.model import parse_pipeline
from controlflow_sdk.pipeline.rowcounts import RowCountError, compute_row_counts


def _terminated_access_graph() -> dict:
    return {"nodes": [
        {"id": "acc", "type": "import", "source_id": "access_accounts"},
        {"id": "active", "type": "filter", "inputs": ["acc"],
         "config": {"logic": "all",
                    "conditions": [{"column": "is_active", "op": "eq", "value": True}]}},
        {"id": "emp", "type": "import", "source_id": "employees"},
        {"id": "term", "type": "filter", "inputs": ["emp"],
         "config": {"logic": "all",
                    "conditions": [{"column": "status", "op": "eq", "value": "terminated"}]}},
        {"id": "join", "type": "join", "inputs": ["active", "term"],
         "config": {"left_key": "employee_id", "right_key": "employee_id", "mode": "inner"}},
        {"id": "tst", "type": "test", "inputs": ["join"],
         "config": {"logic": "any", "severity": "critical", "item_key_column": "account_id",
                    "description_template": "Account {account_id} active for terminated emp",
                    "conditions": [{"column": "account_id", "op": "not_empty"}]}},
    ]}


def test_row_counts_narrow_at_each_joint():
    pipeline = parse_pipeline(_terminated_access_graph())
    # is_active is a REAL bool column (per the Stage-3 gotcha): True/False, not 'true'.
    accounts = pd.DataFrame({
        "account_id": ["A1", "A2", "A3", "A4"],
        "employee_id": ["E1", "E2", "E3", "E4"],
        "is_active": [True, True, False, True],
    })
    employees = pd.DataFrame({
        "employee_id": ["E1", "E2", "E3", "E4"],
        "status": ["terminated", "active", "terminated", "terminated"],
    })
    counts = compute_row_counts(
        pipeline, {"access_accounts": accounts, "employees": employees}
    )
    assert counts["acc"] == 4
    assert counts["active"] == 3            # A1, A2, A4 active
    assert counts["emp"] == 4
    assert counts["term"] == 3              # E1, E3, E4 terminated
    # active accounts whose employee is terminated: A1 (E1) and A4 (E4); A3 inactive.
    assert counts["join"] == 2
    assert counts["tst"] == 2               # both joined rows have a non-empty account_id


def test_row_counts_empty_when_a_source_is_missing():
    pipeline = parse_pipeline(_terminated_access_graph())
    accounts = pd.DataFrame({"account_id": ["A1"], "employee_id": ["E1"], "is_active": [True]})
    # employees frame absent → counter returns {} so the editor shows "—".
    assert compute_row_counts(pipeline, {"access_accounts": accounts}) == {}


def test_row_counts_with_custom_transform_node():
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "je"},
        {"id": "big", "type": "custom_python", "inputs": ["imp"],
         "config": {"flavor": "transform",
                    "code": "rows = rows[rows['amount'].astype(float) >= 100]"}},
        {"id": "tst", "type": "test", "inputs": ["big"],
         "config": {"logic": "any", "item_key_column": "entry_id",
                    "description_template": "Large entry {entry_id}",
                    "conditions": [{"column": "entry_id", "op": "not_empty"}]}},
    ]}
    pipeline = parse_pipeline(graph)
    je = pd.DataFrame({"entry_id": ["E1", "E2", "E3"], "amount": ["50", "100", "250"]})
    counts = compute_row_counts(pipeline, {"je": je})
    assert counts["imp"] == 3
    assert counts["big"] == 2   # amount >= 100 keeps E2, E3
    assert counts["tst"] == 2


def test_row_counts_raises_on_bad_frame():
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "je"},
        {"id": "flt", "type": "filter", "inputs": ["imp"],
         "config": {"logic": "all",
                    "conditions": [{"column": "missing_col", "op": "eq", "value": 1}]}},
        {"id": "tst", "type": "test", "inputs": ["flt"],
         "config": {"logic": "any", "item_key_column": "entry_id",
                    "description_template": "x {entry_id}",
                    "conditions": [{"column": "entry_id", "op": "not_empty"}]}},
    ]}
    pipeline = parse_pipeline(graph)
    je = pd.DataFrame({"entry_id": ["E1"]})
    try:
        compute_row_counts(pipeline, {"je": je})
    except RowCountError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected RowCountError for a missing column")
