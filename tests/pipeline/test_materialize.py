"""materialize_steps: per-node frames; rowcounts equals len over those frames."""
from __future__ import annotations

import pandas as pd

from controlflow_sdk.pipeline.materialize import materialize_steps
from controlflow_sdk.pipeline.model import parse_pipeline
from controlflow_sdk.pipeline.rowcounts import compute_row_counts


def _pipeline():
    # import -> filter(amount>100) -> test(status==open) , plus a 2nd import joined.
    return parse_pipeline({"nodes": [
        {"id": "inv", "type": "import", "source_id": "invoices"},
        {"id": "flt", "type": "filter", "inputs": ["inv"],
         "config": {"logic": "all", "conditions": [
             {"column": "amount", "op": "gt", "value": 100}]}},
        {"id": "tst", "type": "test", "inputs": ["flt"],
         "config": {"logic": "all", "conditions": [
             {"column": "status", "op": "eq", "value": "open"}]}},
    ]})


def _frames():
    return {"invoices": pd.DataFrame({
        "id": ["a", "b", "c", "d"],
        "amount": [50, 150, 200, 300],
        "status": ["open", "open", "closed", "open"],
    })}


def test_materialize_returns_frame_per_node():
    steps = materialize_steps(_pipeline(), _frames())
    assert set(steps) == {"inv", "flt", "tst"}
    assert len(steps["inv"]) == 4          # whole population
    assert len(steps["flt"]) == 3          # amount > 100 → b, c, d
    assert len(steps["tst"]) == 2          # of those, status == open → b, d
    assert list(steps["tst"]["id"]) == ["b", "d"]  # terminal = the violating ROWS


def test_compute_row_counts_equals_len_of_materialized_frames():
    p, f = _pipeline(), _frames()
    counts = compute_row_counts(p, f)
    steps = materialize_steps(p, f)
    assert counts == {nid: len(df) for nid, df in steps.items()}
    assert counts == {"inv": 4, "flt": 3, "tst": 2}


def test_missing_source_returns_empty():
    assert materialize_steps(_pipeline(), {}) == {}
    assert compute_row_counts(_pipeline(), {}) == {}


def test_custom_python_test_terminal_frame_is_the_violations():
    p = parse_pipeline({"nodes": [
        {"id": "src", "type": "import", "source_id": "s"},
        {"id": "cpt", "type": "custom_python", "inputs": ["src"],
         "config": {"flavor": "test", "code":
                    "return [{'item_key': str(r.id), 'description': '', "
                    "'severity': 'low', 'details': {}} "
                    "for r in rows.itertuples() if r.amount > 100]"}},
    ]})
    f = {"s": pd.DataFrame({"id": [1, 2, 3], "amount": [50, 150, 250]})}
    steps = materialize_steps(p, f)
    assert len(steps["cpt"]) == 2          # the two violations, as a frame
    assert compute_row_counts(p, f)["cpt"] == 2
