"""The Builder route context exposes `bands`: a shared Inputs band + per-procedure
bands, each carrying the node view-models that belong in it. Also asserts the
Builder GET renders those bands as the sectioned <details> UI (retired panel gone)."""

from __future__ import annotations

import io
import json
from pathlib import Path

from fastapi.testclient import TestClient

from controlflow_sdk.pipeline.model import parse_pipeline
from controlflow_sdk.plane.routes.pipeline import (
    _card_bands,
    _card_vm,
    _diagram,
    _procedure_context,
)
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect


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


def _seed_with_procedure(client):
    csv = b"user_id,can_create\nU1,true\nU2,\n"
    client.post("/sources", data={"source_id": "users", "format": "csv"},
                files={"file": ("users.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)
    client.post("/controls", data={"id": "c1", "title": "C1", "objective": "o",
                "narrative": "n", "source_ids": ["users"], "failure_threshold_count": "0"},
                follow_redirects=False)
    graph = {
        "nodes": [
            {"id": "src", "type": "import", "source_id": "users"},
            {"id": "tst", "type": "test", "inputs": ["src"],
             "config": {"logic": "all", "procedure_id": "p1",
                        "conditions": [{"column": "can_create", "op": "not_empty"}]}},
        ],
        "procedures": [{"id": "p1", "code": "P1", "name": "One", "position": 0}],
    }
    client.post("/controls/c1/logic/builder",
                data={"pipeline_json": json.dumps(graph)}, follow_redirects=False)


def test_builder_get_renders_sectioned_details_ui(client):
    """The Builder GET renders the sectioned <details> UI (shared Inputs band + one
    per-procedure section header) and NOT the retired Procedures panel."""
    _seed_with_procedure(client)
    page = client.get("/controls/c1/logic/builder").text
    # Sectioned <details> bands present.
    assert "data-proc-section" in page
    assert 'data-band-key="__inputs__"' in page
    assert "data-proc-head" in page
    # The old separate Procedures panel is gone.
    assert "data-proc-panel" not in page


def _forked():
    return parse_pipeline({
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


def test_diagram_exposes_procedure_bands():
    d = _diagram(_forked(), {})
    keys = [b["key"] for b in d["bands"]]
    assert "__inputs__" in keys and "p1" in keys and "p2" in keys
    for b in d["bands"]:
        assert b["row_start"] <= b["row_end"]
        assert b["collapsed"] is False


def test_diagram_collapsed_band_emits_summary_box():
    d = _diagram(_forked(), {}, collapsed=frozenset({"p2"}))
    p2 = next(b for b in d["bands"] if b["key"] == "p2")
    assert p2["collapsed"] is True
    # The collapsed band's private node (t2) is replaced by a single summary box.
    assert not any(box["id"] == "t2" for box in d["boxes"])
    assert any(box.get("summary") and box.get("band") == "p2" for box in d["boxes"])
    # The non-collapsed band's boxes still render.
    assert any(box["id"] == "t1" for box in d["boxes"])


def test_builder_get_degrades_gracefully_on_partial_pipeline(
    client: TestClient, engagement: Path
) -> None:
    """GET /controls/<id>/logic/builder for a control whose stored pipeline JSON
    has a dangling input (parse_pipeline raises PipelineError) must never 500.
    The route returns HTTP 200 and renders the shared Inputs band
    (data-band-key="__inputs__") containing all raw node cards with no
    procedure sections — matching _card_bands(None, vms, empty_ctx) behaviour."""
    # Inject the broken graph directly into the DB, bypassing route validation
    # (the POST endpoint rejects invalid graphs before storing them).
    broken: dict = {
        "nodes": [
            {"id": "src", "type": "import", "source_id": "users", "inputs": [], "config": {}},
            # dangling input — "ghost" never appears as a node id → PipelineError
            {"id": "tst", "type": "test", "inputs": ["ghost"],
             "config": {"logic": "all", "conditions": []}},
        ],
    }
    conn = connect(engagement)
    repo.upsert_control(
        conn,
        id="broken_ctrl",
        title="Broken pipeline control",
        objective="",
        narrative="",
        framework_refs={},
        test_kind="pipeline",
        pipeline=broken,
    )
    conn.close()

    resp = client.get("/controls/broken_ctrl/logic/builder")
    assert resp.status_code == 200
    # Shared Inputs band present (all cards fall here when pipeline is unparsable).
    assert 'data-band-key="__inputs__"' in resp.text
    # No rendered procedure-section <details> elements — check the HTML element
    # pattern (the JS code also contains 'data-proc-section' as a string, so we
    # match the adjacent-attribute pattern that only appears in rendered markup).
    assert 'class="proc-section" data-proc-section' not in resp.text
