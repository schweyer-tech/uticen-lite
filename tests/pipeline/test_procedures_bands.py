"""Unit tests for group_nodes_by_band — the Inputs/per-procedure partition that
both the Builder and the Flowchart consume."""

from __future__ import annotations

from uticen_lite.pipeline.model import parse_pipeline
from uticen_lite.pipeline.procedures import effective_procedures, group_nodes_by_band


def test_shared_import_and_private_branches():
    # src feeds two tests in two procedures → src shared, each test private.
    pipe = parse_pipeline(
        {
            "nodes": [
                {"id": "src", "type": "import", "source_id": "s"},
                {
                    "id": "t1",
                    "type": "test",
                    "inputs": ["src"],
                    "config": {
                        "procedure_id": "p1",
                        "conditions": [{"column": "a", "op": "not_empty"}],
                    },
                },
                {
                    "id": "t2",
                    "type": "test",
                    "inputs": ["src"],
                    "config": {
                        "procedure_id": "p2",
                        "conditions": [{"column": "a", "op": "not_empty"}],
                    },
                },
            ],
            "procedures": [
                {"id": "p1", "code": "P1", "name": "One", "position": 0},
                {"id": "p2", "code": "P2", "name": "Two", "position": 1},
            ],
        }
    )
    bands = group_nodes_by_band(pipe)
    assert bands["shared"] == ["src"]
    assert bands["procedures"] == [
        {"id": "p1", "node_ids": ["t1"]},
        {"id": "p2", "node_ids": ["t2"]},
    ]


def test_shared_filter_stays_shared_private_filter_nests():
    # src → sf (shared filter) → f1 → t1(p1); sf → t2(p2).
    pipe = parse_pipeline(
        {
            "nodes": [
                {"id": "src", "type": "import", "source_id": "s"},
                {
                    "id": "sf",
                    "type": "filter",
                    "inputs": ["src"],
                    "config": {"conditions": [{"column": "a", "op": "not_empty"}]},
                },
                {
                    "id": "f1",
                    "type": "filter",
                    "inputs": ["sf"],
                    "config": {"conditions": [{"column": "b", "op": "not_empty"}]},
                },
                {
                    "id": "t1",
                    "type": "test",
                    "inputs": ["f1"],
                    "config": {
                        "procedure_id": "p1",
                        "conditions": [{"column": "a", "op": "not_empty"}],
                    },
                },
                {
                    "id": "t2",
                    "type": "test",
                    "inputs": ["sf"],
                    "config": {
                        "procedure_id": "p2",
                        "conditions": [{"column": "a", "op": "not_empty"}],
                    },
                },
            ],
            "procedures": [
                {"id": "p1", "code": "P1", "name": "One", "position": 0},
                {"id": "p2", "code": "P2", "name": "Two", "position": 1},
            ],
        }
    )
    bands = group_nodes_by_band(pipe)
    assert bands["shared"] == ["src", "sf"]
    assert bands["procedures"] == [
        {"id": "p1", "node_ids": ["f1", "t1"]},
        {"id": "p2", "node_ids": ["t2"]},
    ]


def test_flattened_band_order_is_topologically_valid():
    pipe = parse_pipeline(
        {
            "nodes": [
                {"id": "src", "type": "import", "source_id": "s"},
                {
                    "id": "sf",
                    "type": "filter",
                    "inputs": ["src"],
                    "config": {"conditions": [{"column": "a", "op": "not_empty"}]},
                },
                {
                    "id": "f1",
                    "type": "filter",
                    "inputs": ["sf"],
                    "config": {"conditions": [{"column": "b", "op": "not_empty"}]},
                },
                {
                    "id": "t1",
                    "type": "test",
                    "inputs": ["f1"],
                    "config": {
                        "procedure_id": "p1",
                        "conditions": [{"column": "a", "op": "not_empty"}],
                    },
                },
                {
                    "id": "t2",
                    "type": "test",
                    "inputs": ["sf"],
                    "config": {
                        "procedure_id": "p2",
                        "conditions": [{"column": "a", "op": "not_empty"}],
                    },
                },
            ],
            "procedures": [
                {"id": "p1", "code": "P1", "name": "One", "position": 0},
                {"id": "p2", "code": "P2", "name": "Two", "position": 1},
            ],
        }
    )
    bands = group_nodes_by_band(pipe)
    flat = bands["shared"] + [nid for b in bands["procedures"] for nid in b["node_ids"]]
    # Every node appears once.
    assert sorted(flat) == sorted(n.id for n in pipe.nodes)
    # A node never precedes one of its inputs in the flattened band order.
    idx = {nid: i for i, nid in enumerate(flat)}
    for n in pipe.nodes:
        for src in n.inputs:
            assert idx[src] < idx[n.id]


def test_orphan_and_no_procedures_fallback():
    # No defined procedures, single import→test: one auto procedure; import shared.
    pipe = parse_pipeline(
        {
            "nodes": [
                {"id": "src", "type": "import", "source_id": "s"},
                {
                    "id": "t",
                    "type": "test",
                    "inputs": ["src"],
                    "config": {"conditions": [{"column": "a", "op": "not_empty"}]},
                },
            ],
        }
    )
    bands = group_nodes_by_band(pipe)
    auto_id = effective_procedures(pipe)[0].id
    assert bands["shared"] == ["src"]
    assert bands["procedures"] == [{"id": auto_id, "node_ids": ["t"]}]


def test_empty_defined_procedure_keeps_its_band():
    pipe = parse_pipeline(
        {
            "nodes": [
                {"id": "src", "type": "import", "source_id": "s"},
                {
                    "id": "t1",
                    "type": "test",
                    "inputs": ["src"],
                    "config": {
                        "procedure_id": "p1",
                        "conditions": [{"column": "a", "op": "not_empty"}],
                    },
                },
            ],
            "procedures": [
                {"id": "p1", "code": "P1", "name": "One", "position": 0},
                {"id": "p2", "code": "P2", "name": "Empty", "position": 1},
            ],
        }
    )
    bands = group_nodes_by_band(pipe)
    assert bands["procedures"] == [
        {"id": "p1", "node_ids": ["t1"]},
        {"id": "p2", "node_ids": []},
    ]
