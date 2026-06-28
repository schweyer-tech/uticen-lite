"""Graph model parse/validate tests (Stage 1, issue #25)."""

from __future__ import annotations

import pytest

from controlflow_sdk.pipeline.model import (
    Pipeline,
    PipelineError,
    parse_pipeline,
)


def _linear_pure() -> dict:
    """One Import → one Filter → terminal Test (the pure single-source shape)."""
    return {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "access_accounts",
             "narrative": "All access accounts"},
            {"id": "flt", "type": "filter", "inputs": ["imp"],
             "narrative": "Keep active accounts",
             "config": {"logic": "all",
                        "conditions": [{"column": "is_active", "op": "eq", "value": True}]}},
            {"id": "tst", "type": "test", "inputs": ["flt"],
             "narrative": "Flag privileged",
             "config": {"logic": "all", "severity": "high",
                        "conditions": [{"column": "is_privileged", "op": "eq", "value": True}],
                        "item_key_column": "account_id"}},
        ]
    }


def test_parse_linear_pipeline_round_trips_nodes():
    pipe = parse_pipeline(_linear_pure())
    assert isinstance(pipe, Pipeline)
    assert [n.id for n in pipe.nodes] == ["imp", "flt", "tst"]
    assert pipe.terminal.id == "tst"
    assert pipe.import_source_ids() == ["access_accounts"]


def test_import_node_has_no_inputs_and_a_source():
    pipe = parse_pipeline(_linear_pure())
    imp = pipe.node("imp")
    assert imp.inputs == []
    assert imp.source_id == "access_accounts"


def test_topological_order_is_dependency_respecting():
    pipe = parse_pipeline(_linear_pure())
    order = [n.id for n in pipe.topological()]
    assert order.index("imp") < order.index("flt") < order.index("tst")


def test_rejects_missing_terminal_test():
    raw = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "s"},
            {"id": "flt", "type": "filter", "inputs": ["imp"],
             "config": {"logic": "all", "conditions": []}},
        ]
    }
    with pytest.raises(PipelineError, match="feeds nothing|terminal"):
        parse_pipeline(raw)


def test_pipeline_allows_two_terminal_tests():
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "s"},
        {"id": "flt", "type": "filter", "inputs": ["imp"],
         "config": {"logic": "all", "conditions": [
             {"column": "status", "op": "eq", "value": "posted"},
         ]}},
        {"id": "a", "type": "test", "inputs": ["flt"],
         "config": {"logic": "all", "conditions": [{"column": "approver", "op": "is_empty"}]}},
        {"id": "b", "type": "test", "inputs": ["flt"],
         "config": {"logic": "all", "conditions": [{"column": "po", "op": "is_empty"}]}},
    ]}
    p = parse_pipeline(graph)
    assert [t.id for t in p.terminals] == ["a", "b"]
    assert p.terminal.id == "a"  # back-compat: first terminal


def test_pipeline_rejects_non_test_sink():
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "s"},
        {"id": "flt", "type": "filter", "inputs": ["imp"],
         "config": {"logic": "all", "conditions": [{"column": "x", "op": "is_empty"}]}},
        {"id": "tst", "type": "test", "inputs": ["imp"],
         "config": {"logic": "all", "conditions": [{"column": "x", "op": "is_empty"}]}},
    ]}  # flt is a dangling non-test sink
    with pytest.raises(PipelineError, match="feeds nothing"):
        parse_pipeline(graph)


def test_single_terminal_back_compat_unchanged():
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "s"},
        {"id": "tst", "type": "test", "inputs": ["imp"],
         "config": {"logic": "all", "conditions": [{"column": "x", "op": "is_empty"}]}},
    ]}
    p = parse_pipeline(graph)
    assert [t.id for t in p.terminals] == ["tst"]
    assert p.terminal.id == "tst"


def test_rejects_cycle():
    raw = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "s"},
            {"id": "a", "type": "filter", "inputs": ["b"],
             "config": {"logic": "all", "conditions": []}},
            {"id": "b", "type": "filter", "inputs": ["a"],
             "config": {"logic": "all", "conditions": []}},
            {"id": "t", "type": "test", "inputs": ["b"],
             "config": {"logic": "all", "conditions": []}},
        ]
    }
    with pytest.raises(PipelineError, match="cycle"):
        parse_pipeline(raw)


def test_rejects_dangling_input():
    raw = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "s"},
            {"id": "t", "type": "test", "inputs": ["ghost"],
             "config": {"logic": "all", "conditions": []}},
        ]
    }
    with pytest.raises(PipelineError, match="unknown input|ghost"):
        parse_pipeline(raw)


def test_rejects_unknown_source_when_validated_against_known_set():
    pipe = parse_pipeline(_linear_pure())
    with pytest.raises(PipelineError, match="unknown source|access_accounts"):
        pipe.validate_sources({"employees", "payments"})


def test_validate_sources_passes_when_known():
    pipe = parse_pipeline(_linear_pure())
    pipe.validate_sources({"access_accounts", "employees"})  # no raise


def test_rejects_unknown_node_type():
    raw = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "s"},
            {"id": "agg", "type": "aggregate", "inputs": ["imp"], "config": {}},
            {"id": "t", "type": "test", "inputs": ["agg"],
             "config": {"logic": "all", "conditions": []}},
        ]
    }
    with pytest.raises(PipelineError, match="aggregate|unknown node type"):
        parse_pipeline(raw)


def test_rejects_duplicate_node_ids():
    raw = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "s"},
            {"id": "imp", "type": "filter", "inputs": ["imp"],
             "config": {"logic": "all", "conditions": []}},
            {"id": "t", "type": "test", "inputs": ["imp"],
             "config": {"logic": "all", "conditions": []}},
        ]
    }
    with pytest.raises(PipelineError, match="duplicate"):
        parse_pipeline(raw)


def test_import_requires_source_id():
    raw = {
        "nodes": [
            {"id": "imp", "type": "import"},
            {"id": "t", "type": "test", "inputs": ["imp"],
             "config": {"logic": "all", "conditions": []}},
        ]
    }
    with pytest.raises(PipelineError, match="source_id"):
        parse_pipeline(raw)


def test_join_requires_two_inputs():
    raw = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "a"},
            {"id": "j", "type": "join", "inputs": ["imp"],
             "config": {"left_key": "x", "right_key": "y", "mode": "inner"}},
            {"id": "t", "type": "test", "inputs": ["j"],
             "config": {"logic": "all", "conditions": []}},
        ]
    }
    with pytest.raises(PipelineError, match="two inputs|Join"):
        parse_pipeline(raw)


def test_custom_python_flavor_must_be_known():
    raw = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "a"},
            {"id": "c", "type": "custom_python", "inputs": ["imp"],
             "config": {"code": "rows = rows", "flavor": "bogus"}},
            {"id": "t", "type": "test", "inputs": ["c"],
             "config": {"logic": "all", "conditions": []}},
        ]
    }
    with pytest.raises(PipelineError, match="flavor"):
        parse_pipeline(raw)
