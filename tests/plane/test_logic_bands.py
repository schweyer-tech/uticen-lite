"""The Builder route context exposes `bands`: a shared Inputs band + per-procedure
bands, each carrying the node view-models that belong in it."""

from __future__ import annotations

from controlflow_sdk.pipeline.model import parse_pipeline
from controlflow_sdk.plane.routes.pipeline import _card_bands, _card_vm, _procedure_context


def _vms(pipeline):
    return [_card_vm(n, pipeline, {}, {}, {}) for n in pipeline.topological()]


def test_card_bands_groups_vms_by_procedure():
    pipe = parse_pipeline({
        "nodes": [
            {"id": "src", "type": "import", "source_id": "s"},
            {"id": "t1", "type": "test", "inputs": ["src"],
             "config": {"procedure_id": "p1", "conditions": [{"column": "a", "op": "not_empty"}]}},
            {"id": "t2", "type": "test", "inputs": ["src"],
             "config": {"procedure_id": "p2", "conditions": [{"column": "a", "op": "not_empty"}]}},
        ],
        "procedures": [
            {"id": "p1", "code": "P1", "name": "One", "position": 0},
            {"id": "p2", "code": "P2", "name": "Two", "position": 1},
        ],
    })
    bands = _card_bands(pipe, _vms(pipe), _procedure_context(pipe))
    assert bands["shared"]["key"] == "__inputs__"
    assert [vm["id"] for vm in bands["shared"]["nodes"]] == ["src"]
    assert [b["key"] for b in bands["procedures"]] == ["p1", "p2"]
    assert [vm["id"] for vm in bands["procedures"][0]["nodes"]] == ["t1"]
    assert bands["procedures"][0]["proc"]["code"] == "P1"


def test_card_bands_unparsable_pipeline_all_shared():
    vms = [{"id": "x"}, {"id": "y"}]
    empty_ctx = {"procedures": [], "node_procedures": {}, "selected_procedure_for": {}}
    bands = _card_bands(None, vms, empty_ctx)
    assert bands["procedures"] == []
    assert [vm["id"] for vm in bands["shared"]["nodes"]] == ["x", "y"]
