"""Server-rendered pipeline editor (issue #25, Stage 3 — §5/§9/§11).

Covers the Stage-3 UI acceptance: the editor renders node cards + the SVG
diagram for a saved pipeline; authoring the terminated-access pipeline via the
routes yields a runnable control whose compile+run gives the right exceptions and
whose export validates against the bundle schema; live row-counts appear;
"Convert to Python test" sets test_kind=python and prefills the compiled code; a
Custom node with open() shows the inline offramp error; and the new sub-route is
not shadowed by /controls/{control_id} (learning 0007).
"""

from __future__ import annotations

import io
import json
import re

from controlflow_sdk.pipeline.lint import OFFRAMP_MESSAGE

_OFFRAMP_STABLE = "pull data in with an Import node, or convert this control"
assert _OFFRAMP_STABLE in OFFRAMP_MESSAGE


# --- fixtures helpers -------------------------------------------------------

def _make_source(client, sid, csv):
    client.post("/sources", data={"source_id": sid, "format": "csv"},
                files={"file": (f"{sid}.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)


def _conn(client):
    from controlflow_sdk.store.db import connect
    return connect(client.app.state.project_root)


def _make_control(client, cid="C1"):
    """Create a bare control we can attach a pipeline to."""
    client.post("/controls", data={
        "id": cid, "title": "Term Access", "objective": "o", "narrative": "n",
        "test_kind": "rule", "rule_logic": "all",
    }, follow_redirects=False)


def _terminated_access_graph() -> dict:
    return {"nodes": [
        {"id": "acc", "type": "import", "source_id": "access_accounts",
         "narrative": "All access accounts"},
        {"id": "active", "type": "filter", "inputs": ["acc"],
         "narrative": "Keep active accounts",
         "config": {"logic": "all",
                    "conditions": [{"column": "is_active", "op": "eq", "value": True}]}},
        {"id": "emp", "type": "import", "source_id": "employees"},
        {"id": "term", "type": "filter", "inputs": ["emp"],
         "narrative": "Keep terminated employees",
         "config": {"logic": "all",
                    "conditions": [{"column": "status", "op": "eq", "value": "terminated"}]}},
        {"id": "join", "type": "join", "inputs": ["active", "term"],
         "narrative": "Active accounts of terminated employees",
         "config": {"left_key": "employee_id", "right_key": "employee_id", "mode": "inner"}},
        {"id": "tst", "type": "test", "inputs": ["join"],
         "config": {"logic": "any", "severity": "critical", "item_key_column": "account_id",
                    "description_template": "Account {account_id} active for terminated employee",
                    "conditions": [{"column": "account_id", "op": "not_empty"}]}},
    ]}


def _type_column_boolean(client, sid, col):
    """Map a source column's data_type to boolean (as an analyst would in the
    source editor) so it loads as a REAL bool dtype — the Stage-3 gotcha: the
    Filter value for a bool column is python True, surfaced as a typed value."""
    from controlflow_sdk.store import repo
    conn = _conn(client)
    src = repo.get_source(conn, sid)
    cols = [dict(c) for c in src["columns"]]
    for c in cols:
        if c["original_name"] == col:
            c["data_type"] = "boolean"
    repo.set_columns(conn, sid, cols)
    conn.close()


def _seed_terminated_access(client):
    # is_active is typed boolean → loads as a real bool dtype (True/False), so
    # the Filter value=True matches in BOTH the run and the live row-counts.
    _make_source(client, "access_accounts",
                 b"account_id,employee_id,is_active,system\n"
                 b"A1,E1,true,CRM\nA2,E2,true,ERP\nA3,E3,false,CRM\nA4,E4,true,CRM\n")
    _type_column_boolean(client, "access_accounts", "is_active")
    _make_source(client, "employees",
                 b"employee_id,status\nE1,terminated\nE2,active\nE3,terminated\nE4,terminated\n")


def _save_pipeline(client, cid, graph):
    return client.post(f"/controls/{cid}/pipeline",
                       data={"pipeline_json": json.dumps(graph)},
                       follow_redirects=False)


# --- tests ------------------------------------------------------------------

def test_pipeline_tab_renders_cards_and_diagram(client):
    _seed_terminated_access(client)
    _make_control(client, "C1")
    assert _save_pipeline(client, "C1", _terminated_access_graph()).status_code in (302, 303)

    resp = client.get("/controls/C1/pipeline")
    assert resp.status_code == 200
    body = resp.text
    # A card per node (id surfaced) + the typed kind chip.
    for nid in ("acc", "active", "emp", "term", "join", "tst"):
        assert f'data-node="{nid}"' in body
    # Join card names BOTH input streams (fan-in by id, not drawn wires).
    assert "Left input" in body and "Right input" in body
    # The generated SVG flowchart is server-rendered.
    assert "Pipeline flowchart" in body and "<svg" in body
    # The fan-in (Join's two inputs can't both sit directly above it) is routed
    # through the left gutter as a curved path, not stacked on the center spine,
    # so converging edges stay legible. Guards the diagram-routing regression.
    assert re.search(r'class="fc-edge"[^>]*d="M[^"]* C ', body)
    # The read-only generated-Python glass-box + the convert offramp.
    assert "Generated Python" in body
    assert "Convert to Python test" in body


def test_pipeline_editor_shows_live_row_counts(client):
    _seed_terminated_access(client)
    _make_control(client, "C1")
    _save_pipeline(client, "C1", _terminated_access_graph())
    body = client.get("/controls/C1/pipeline").text
    # 4 accounts → 3 active → join 2 → test 2. The counts narrow at each joint.
    assert "rows: <strong>4</strong>" in body      # acc import
    assert "rows: <strong>3</strong>" in body      # active filter
    assert "rows: <strong>2</strong>" in body      # join / test


def test_authoring_terminated_access_pipeline_runs_with_right_exceptions(client):
    _seed_terminated_access(client)
    _make_control(client, "C1")
    _save_pipeline(client, "C1", _terminated_access_graph())

    from controlflow_sdk.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "C1")
    conn.close()
    assert c["test_kind"] == "pipeline"
    # Source binding derived from the Import nodes, in node order.
    assert c["source_ids"] == ["access_accounts", "employees"]
    # Cross-source → compiled to a test() string the runner understands.
    assert c["test_code"] is not None and "def test(pop, sources):" in c["test_code"]

    # Run full-population via the store run path; A1 and A4 are exceptions.
    resp = client.post("/controls/C1/run", follow_redirects=False)
    assert resp.status_code in (302, 303)
    location = resp.headers["location"]
    run_id = location.rsplit("/", 1)[-1]
    conn = _conn(client)
    run = repo.get_run(conn, run_id)
    conn.close()
    keys = sorted(v["item_key"] for v in run["violations"])
    assert keys == ["A1", "A4"]


def test_pipeline_control_exports_against_schema(client):
    _seed_terminated_access(client)
    _make_control(client, "C1")
    _save_pipeline(client, "C1", _terminated_access_graph())
    client.post("/controls/C1/run", follow_redirects=False)

    resp = client.post("/export", follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    # The exported bundle never contains the store-only graph vocabulary.
    import zipfile
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        manifest_name = next(n for n in names if n.endswith("manifest.json"))
        manifest = zf.read(manifest_name).decode("utf-8")
    assert '"node"' not in manifest and "pipeline" not in manifest.lower()


def test_convert_to_python_sets_kind_and_prefills_code(client):
    _seed_terminated_access(client)
    _make_control(client, "C1")
    _save_pipeline(client, "C1", _terminated_access_graph())

    resp = client.post("/controls/C1/pipeline/convert", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert resp.headers["location"] == "/controls/C1"

    from controlflow_sdk.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "C1")
    conn.close()
    # The one-way door: kind becomes python, code is the stitched test(), graph dropped.
    assert c["test_kind"] == "python"
    assert c["test_code"] is not None and "def test(pop, sources):" in c["test_code"]
    assert c["pipeline"] is None
    # The escape-hatch editor renders the prefilled code.
    body = client.get("/controls/C1").text
    assert "def test(pop, sources):" in body


def test_convert_pure_pipeline_yields_runnable_test(client):
    """A pure single-source pipeline compiles to a rule_spec, but the offramp
    must still graduate to a RUNNABLE test() (lossless) — not a bare comment."""
    _make_source(client, "accounts",
                 b"account_id,is_privileged\nA1,true\nA2,false\n")
    _make_control(client, "C3")
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "accounts"},
        {"id": "tst", "type": "test", "inputs": ["imp"],
         "config": {"logic": "all", "severity": "high", "item_key_column": "account_id",
                    "description_template": "Privileged {account_id}",
                    "conditions": [{"column": "is_privileged", "op": "eq", "value": "true"}]}},
    ]}
    _save_pipeline(client, "C3", graph)

    from controlflow_sdk.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "C3")
    conn.close()
    # Saved as a pipeline that compiled to a rule_spec (stays no-code in bundle).
    assert c["test_kind"] == "pipeline" and c["rule_spec"] is not None

    client.post("/controls/C3/pipeline/convert", follow_redirects=False)
    conn = _conn(client)
    c = repo.get_control(conn, "C3")
    conn.close()
    assert c["test_kind"] == "python"
    assert "def test(pop, sources):" in c["test_code"]
    # And it actually runs (the lossless graduation), flagging A1.
    resp = client.post("/controls/C3/run", follow_redirects=False)
    run_id = resp.headers["location"].rsplit("/", 1)[-1]
    conn = _conn(client)
    run = repo.get_run(conn, run_id)
    conn.close()
    assert sorted(v["item_key"] for v in run["violations"]) == ["A1"]


def test_custom_node_with_open_shows_inline_offramp_error(client):
    _make_source(client, "je", b"entry_id,amount\nE1,100\n")
    _make_control(client, "C2")
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "je"},
        {"id": "cust", "type": "custom_python", "inputs": ["imp"],
         "config": {"flavor": "transform", "code": "rows = open('/etc/passwd').read()"}},
        {"id": "tst", "type": "test", "inputs": ["cust"],
         "config": {"logic": "any", "item_key_column": "entry_id",
                    "description_template": "x {entry_id}",
                    "conditions": [{"column": "entry_id", "op": "not_empty"}]}},
    ]}
    resp = _save_pipeline(client, "C2", graph)
    # Refused (422) — re-rendered editor with the inline offramp, not persisted.
    assert resp.status_code == 422
    assert _OFFRAMP_STABLE in resp.text
    # The error is pinned on the offending node card (inline), not just a banner.
    assert "node-error" in resp.text
    from controlflow_sdk.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "C2")
    conn.close()
    # The control existed but its graph was NOT updated to the bad one.
    assert c["pipeline"] is None


def test_pipeline_subroute_not_shadowed_by_catch_all(client):
    """GET /controls/{id}/pipeline resolves to the editor, not the definition
    catch-all (learning 0007 route-ordering)."""
    routes = [r for r in client.app.router.routes
              if getattr(r, "path", "") == "/controls/{control_id}/pipeline"
              and "GET" in getattr(r, "methods", set())]
    assert routes, "pipeline GET sub-route is registered"
    paths = [getattr(r, "path", "") for r in client.app.router.routes]
    assert paths.index("/controls/{control_id}/pipeline") < paths.index(
        "/controls/{control_id}"
    ), "pipeline sub-route must precede the /{control_id} catch-all"
