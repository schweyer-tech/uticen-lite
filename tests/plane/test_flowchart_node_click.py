"""Clicking a node in the flowchart opens the step inspector. It must degrade to
a friendly "can't inspect" page on ANY unexpected error — never a 500 — honoring
the derived/in-progress-graph contract (learning 0013) and _materialize_full's
own "never raises into the request" docstring.

Review (2026-06-27): "When I click on a node in the flowchart, I either get an
internal server error or this: <screenshot>." A specific natural trigger could
not be reproduced across the demo + malformed graphs, so the request boundary is
hardened to honor the documented contract.
"""
import io
import json

from uticen_lite.plane.routes import pipeline as P


def _seed_pipeline_control(client):
    client.post(
        "/sources",
        data={"source_id": "users", "format": "csv"},
        files={"file": ("users.csv", io.BytesIO(b"user_id,can_create\nU1,true\n"), "text/csv")},
        follow_redirects=False,
    )
    client.post(
        "/controls",
        data={"id": "sod", "title": "SoD", "objective": "o", "narrative": "n",
              "source_ids": ["users"], "failure_threshold_count": "0"},
        follow_redirects=False,
    )
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "users"},
        {"id": "tst", "type": "test", "inputs": ["imp"],
         "config": {"logic": "all", "severity": "high", "item_key_column": "user_id",
                    "description_template": "User {user_id}",
                    "conditions": [{"column": "can_create", "op": "eq", "value": True}]}},
    ]}
    client.post("/controls/sod/logic/builder",
                data={"pipeline_json": json.dumps(graph)}, follow_redirects=False)


def test_node_click_renders_for_a_normal_pipeline(client):
    _seed_pipeline_control(client)
    r = client.get("/controls/sod/logic/step/imp/data")
    assert r.status_code == 200, r.text


def test_node_click_degrades_when_materialize_raises(client, monkeypatch):
    _seed_pipeline_control(client)

    def boom(*_a, **_k):
        raise RuntimeError("unexpected materialize failure")

    # Force an unexpected (non-MaterializeError) failure deep in the inspector path.
    monkeypatch.setattr(P, "_materialize_full", boom)
    r = client.get("/controls/sod/logic/step/imp/data")
    assert r.status_code != 500, r.text
    assert r.status_code == 200
    # the friendly "can't inspect this step" page, not a stack trace
    assert "inspect" in r.text.lower() or "not computable" in r.text.lower()


def test_node_click_degrades_when_pipeline_parse_raises(client, monkeypatch):
    _seed_pipeline_control(client)

    def boom(*_a, **_k):
        raise RuntimeError("unexpected parse failure")

    monkeypatch.setattr(P, "_pipeline_for_view", boom)
    r = client.get("/controls/sod/logic/step/imp/data")
    assert r.status_code != 500, r.text


def test_materialize_full_never_raises(client, monkeypatch):
    """The contract in the docstring: _materialize_full returns {} on any failure."""
    _seed_pipeline_control(client)
    import uticen_lite.pipeline.materialize as M

    def boom(*_a, **_k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(M, "materialize_steps", boom)
    from uticen_lite.store.db import connect
    root = client.app.state.project_root
    conn = connect(root)
    try:
        from uticen_lite.store import repo
        pipeline = P._pipeline_for_view(repo.get_control(conn, "sod"))
        assert pipeline is not None
        assert P._materialize_full(conn, root, pipeline) == {}
    finally:
        conn.close()
