"""The Run button must render a workpaper for a ready control and degrade to a
friendly message — never a 500 — for a half-authored one. It is also available
on the drilled-down control editor, not only the dashboard.

Review (2026-06-27): "when a user clicks the run button it should render a
workpaper. Right now, they just get an internal server error. The run button
should also be available on the drilled down control page."
"""
import io
import json

from uticen_lite.store import repo
from uticen_lite.store.db import connect


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


# ---------------------------------------------------------------------------
# Corrupted persisted state from ordinary authoring mistakes must degrade to a
# friendly 422 page — never a raw 500 (2026-06-27 review, learning 0013).
# ---------------------------------------------------------------------------


def _corrupt_rule_control(client, rule_spec, source_ids=("users",), cid="bad"):
    """Persist a control whose rule_spec evaluates to an error, bypassing the builder."""
    conn = connect(client.app.state.project_root)
    try:
        repo.upsert_control(
            conn, id=cid, title="Bad", objective="o", narrative="n",
            framework_refs={}, test_kind="rule", rule_spec=rule_spec,
            failure_threshold_count=0,
        )
        repo.set_control_sources(conn, cid, list(source_ids))
    finally:
        conn.close()


def test_run_cross_source_unknown_source_is_friendly_not_500(client):
    # A cross-source condition whose other_source was deleted/renamed → ValueError.
    _make_source(client, "users", b"user_id\nU1\nU2\n")
    _corrupt_rule_control(client, {
        "logic": "all",
        "conditions": [{"op": "not_exists_in", "column": "user_id",
                        "other_source": "ghost", "this_key": "user_id",
                        "other_key": "employee_id"}],
        "severity": "high", "description_template": "User {user_id}",
        "item_key_column": "user_id"})
    resp = client.post("/controls/bad/run", follow_redirects=False)
    assert resp.status_code == 422, resp.text


def test_run_comparison_on_text_column_is_friendly_not_500(client):
    # A gt comparison on a text column (type mismatch) → pandas TypeError.
    _make_source(client, "users", b"user_id\nU1\nU2\n")
    _corrupt_rule_control(client, {
        "logic": "all",
        "conditions": [{"column": "user_id", "op": "gt", "value": 5}],
        "severity": "high", "description_template": "User {user_id}",
        "item_key_column": "user_id"})
    resp = client.post("/controls/bad/run", follow_redirects=False)
    assert resp.status_code == 422, resp.text


def test_run_invalid_regex_is_friendly_not_500(client):
    # An invalid regex pattern → ArrowInvalid / re.error during evaluation.
    _make_source(client, "users", b"user_id\nU1\nU2\n")
    _corrupt_rule_control(client, {
        "logic": "all",
        "conditions": [{"column": "user_id", "op": "regex", "value": "("}],
        "severity": "high", "description_template": "User {user_id}",
        "item_key_column": "user_id"})
    resp = client.post("/controls/bad/run", follow_redirects=False)
    assert resp.status_code == 422, resp.text


def test_run_missing_data_file_is_friendly_not_500(client):
    # A bound source whose backing CSV is gone from disk → FileNotFoundError.
    _make_source(client, "users", b"user_id,can_create\nU1,true\n")
    (client.app.state.project_root / "data" / "users.csv").unlink()
    _corrupt_rule_control(client, {
        "logic": "all",
        "conditions": [{"column": "can_create", "op": "eq", "value": True}],
        "severity": "high", "description_template": "User {user_id}",
        "item_key_column": "user_id"})
    resp = client.post("/controls/bad/run", follow_redirects=False)
    assert resp.status_code == 422, resp.text


def test_run_unexpected_error_is_friendly_not_500(client, monkeypatch):
    # Layer-2 backstop: even an unforeseen exception must not 500 the Run button.
    _rule_control(client)

    def boom(*args, **kwargs):
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(
        "uticen_lite.plane.routes.runs.run_control_in_store", boom
    )
    resp = client.post("/controls/sod/run", follow_redirects=False)
    assert resp.status_code == 422, resp.text


def test_run_view_missing_run_is_friendly_not_500(client):
    # A run-view for a non-existent run_id must degrade, never 500.
    _rule_control(client)
    resp = client.get("/controls/sod/runs/deadbeefdeadbeef")
    assert resp.status_code != 500, resp.text
