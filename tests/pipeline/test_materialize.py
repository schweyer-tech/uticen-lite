"""materialize_steps: per-node frames; rowcounts equals len over those frames."""

from __future__ import annotations

import copy

import pandas as pd

from uticen_lite.pipeline.materialize import _step_keys, materialize_steps, new_step_cache
from uticen_lite.pipeline.model import parse_pipeline
from uticen_lite.pipeline.rowcounts import compute_row_counts


def _pipeline():
    # import -> filter(amount>100) -> test(status==open) , plus a 2nd import joined.
    return parse_pipeline(
        {
            "nodes": [
                {"id": "inv", "type": "import", "source_id": "invoices"},
                {
                    "id": "flt",
                    "type": "filter",
                    "inputs": ["inv"],
                    "config": {
                        "logic": "all",
                        "conditions": [{"column": "amount", "op": "gt", "value": 100}],
                    },
                },
                {
                    "id": "tst",
                    "type": "test",
                    "inputs": ["flt"],
                    "config": {
                        "logic": "all",
                        "conditions": [{"column": "status", "op": "eq", "value": "open"}],
                    },
                },
            ]
        }
    )


def _frames():
    return {
        "invoices": pd.DataFrame(
            {
                "id": ["a", "b", "c", "d"],
                "amount": [50, 150, 200, 300],
                "status": ["open", "open", "closed", "open"],
            }
        )
    }


def test_materialize_returns_frame_per_node():
    steps = materialize_steps(_pipeline(), _frames())
    assert set(steps) == {"inv", "flt", "tst"}
    assert len(steps["inv"]) == 4  # whole population
    assert len(steps["flt"]) == 3  # amount > 100 → b, c, d
    assert len(steps["tst"]) == 2  # of those, status == open → b, d
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
    p = parse_pipeline(
        {
            "nodes": [
                {"id": "src", "type": "import", "source_id": "s"},
                {
                    "id": "cpt",
                    "type": "custom_python",
                    "inputs": ["src"],
                    "config": {
                        "flavor": "test",
                        "code": "return [{'item_key': str(r.id), 'description': '', "
                        "'severity': 'low', 'details': {}} "
                        "for r in rows.itertuples() if r.amount > 100]",
                    },
                },
            ]
        }
    )
    f = {"s": pd.DataFrame({"id": [1, 2, 3], "amount": [50, 150, 250]})}
    steps = materialize_steps(p, f)
    assert len(steps["cpt"]) == 2  # the two violations, as a frame
    assert compute_row_counts(p, f)["cpt"] == 2


def _graph():
    return {
        "nodes": [
            {"id": "inv", "type": "import", "source_id": "invoices"},
            {
                "id": "flt",
                "type": "filter",
                "inputs": ["inv"],
                "config": {
                    "logic": "all",
                    "conditions": [{"column": "amount", "op": "gt", "value": 100}],
                },
            },
            {
                "id": "tst",
                "type": "test",
                "inputs": ["flt"],
                "config": {
                    "logic": "all",
                    "conditions": [{"column": "status", "op": "eq", "value": "open"}],
                },
            },
        ]
    }


def test_step_keys_change_for_edited_node_and_descendants_only():
    p1 = parse_pipeline(_graph())
    sv = {"invoices": "v1"}
    k1 = _step_keys(p1, sv)

    g2 = copy.deepcopy(_graph())  # edit the FILTER (a middle node)
    g2["nodes"][1]["config"]["conditions"][0]["value"] = 200
    k2 = _step_keys(parse_pipeline(g2), sv)

    assert k2["inv"] == k1["inv"]  # upstream unchanged
    assert k2["flt"] != k1["flt"]  # edited node changed
    assert k2["tst"] != k1["tst"]  # descendant changed


def test_source_version_change_busts_every_key():
    p = parse_pipeline(_graph())
    k1 = _step_keys(p, {"invoices": "v1"})
    k2 = _step_keys(p, {"invoices": "v2"})
    assert all(k2[n] != k1[n] for n in k1)


def test_cache_recomputes_only_edited_step_onward():
    cache = new_step_cache()
    sv = {"invoices": "v1"}
    first = set()
    materialize_steps(
        parse_pipeline(_graph()), _frames(), source_versions=sv, cache=cache, recomputed_out=first
    )
    assert first == {"inv", "flt", "tst"}  # cold cache → all recompute

    g2 = copy.deepcopy(_graph())
    g2["nodes"][2]["config"]["conditions"][0]["value"] = "closed"  # edit the TEST only
    second = set()
    steps = materialize_steps(
        parse_pipeline(g2), _frames(), source_versions=sv, cache=cache, recomputed_out=second
    )
    assert second == {"tst"}  # only the edited terminal recomputed
    # ...and the cached path is still correct (status==closed → only row c):
    assert list(steps["tst"]["id"]) == ["c"]


def test_cache_is_bounded():
    from uticen_lite.pipeline.materialize import _CACHE_MAX

    cache = new_step_cache()
    for i in range(_CACHE_MAX + 20):
        materialize_steps(
            parse_pipeline(_graph()), _frames(), source_versions={"invoices": f"v{i}"}, cache=cache
        )
    assert 0 < len(cache) <= _CACHE_MAX
