"""The Run button must render a workpaper for a ready control and degrade to a
friendly message — never a 500 — for a half-authored one. It is also available
on the drilled-down control editor, not only the dashboard.

Review (2026-06-27): "when a user clicks the run button it should render a
workpaper. Right now, they just get an internal server error. The run button
should also be available on the drilled down control page."
"""
import io
import json


def _make_source(client, sid="users", csv=b"user_id,can_create\nU1,true\n"):
    client.post(
        "/sources",
        data={"source_id": sid, "format": "csv"},
        files={"file": (f"{sid}.csv", io.BytesIO(csv), "text/csv")},
        follow_redirects=False,
    )


def _rule_control(client):
    _make_source(client, "users", b"user_id,can_create,can_approve\nU1,true,true\nU2,true,false\n")
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
                    "conditions": [{"column": "can_create", "op": "eq", "value": True},
                                   {"column": "can_approve", "op": "eq", "value": True}]}},
    ]}
    client.post("/controls/sod/logic/builder",
                data={"pipeline_json": json.dumps(graph)}, follow_redirects=False)


def test_run_ready_control_renders_workpaper(client):
    _rule_control(client)
    resp = client.post("/controls/sod/run", follow_redirects=True)
    assert resp.status_code == 200, resp.text
    assert "Run results" in resp.text
    assert "Workpaper" in resp.text          # the embedded workpaper section


def test_run_control_without_sources_is_friendly_not_500(client):
    # A control with no bound data source (half-authored) used to IndexError → 500.
    client.post(
        "/controls",
        data={"id": "nosrc", "title": "NoSrc", "objective": "o", "narrative": "n",
              "failure_threshold_count": "0"},
        follow_redirects=False,
    )
    resp = client.post("/controls/nosrc/run", follow_redirects=False)
    assert resp.status_code != 500, resp.text
    assert "ready to run" in resp.text.lower() or "data source" in resp.text.lower()


def test_run_control_without_logic_is_friendly_not_500(client):
    # A control bound to a source but with no authored logic used to ProjectError → 500.
    _make_source(client, "users")
    client.post(
        "/controls",
        data={"id": "nologic", "title": "NoLogic", "objective": "o", "narrative": "n",
              "source_ids": ["users"], "failure_threshold_count": "0"},
        follow_redirects=False,
    )
    resp = client.post("/controls/nologic/run", follow_redirects=False)
    assert resp.status_code != 500, resp.text
    assert "ready to run" in resp.text.lower()


def test_control_editor_has_run_button(client):
    _rule_control(client)
    page = client.get("/controls/sod")
    assert page.status_code == 200
    # a Run affordance posting to the run route, available on the control page itself
    assert 'action="/controls/sod/run"' in page.text
    # exactly ONE Run form — guards against the duplicate-block merge artifact
    assert page.text.count('action="/controls/sod/run"') == 1
