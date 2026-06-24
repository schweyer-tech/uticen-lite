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
import zipfile

import pytest

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
    return client.post(f"/controls/{cid}/logic/builder",
                       data={"pipeline_json": json.dumps(graph)},
                       follow_redirects=False)


# --- tests ------------------------------------------------------------------

def test_pipeline_tab_renders_cards_and_diagram(client):
    _seed_terminated_access(client)
    _make_control(client, "C1")
    assert _save_pipeline(client, "C1", _terminated_access_graph()).status_code in (302, 303)

    # Builder pane: node cards (the /pipeline redirect lands here).
    builder_body = client.get("/controls/C1/logic/builder").text
    # A card per node (id surfaced) + the typed kind chip.
    for nid in ("acc", "active", "emp", "term", "join", "tst"):
        assert f'data-node="{nid}"' in builder_body
    # Join card names BOTH input streams (fan-in by id, not drawn wires).
    assert "Left input" in builder_body and "Right input" in builder_body

    # Flowchart pane: the generated SVG (now lives on its own tab).
    fc_body = client.get("/controls/C1/logic/flowchart").text
    assert "Pipeline flowchart" in fc_body and "<svg" in fc_body
    # Multi-lane layout: the Join's two feeder branches sit in SEPARATE columns
    # that converge at the Join. The flowchart positions the two import roots at
    # DIFFERENT x coordinates (distinct lanes), so the join no longer reads as a
    # single linear chain. Guards the multi-lane layout regression.
    rect_xs = [int(m) for m in re.findall(r'<rect x="(\d+)"', fc_body)]
    assert len(set(rect_xs)) >= 2, f"expected >=2 distinct lanes, got xs={rect_xs}"
    # At least one fan-in edge crosses lanes: it converges from a branch column
    # into the spine, drawn as an S-curve (presentation-only — execution order is
    # unchanged and still topological).
    assert re.search(r'class="fc-edge"[^>]*d="M[^"]* C ', fc_body)
    # The read-only generated-Python glass-box + the convert offramp live on the
    # python tab; verify via the old control_pipeline template still served there.
    py_body = client.get("/controls/C1/logic/python").text
    assert "Generated Python" in py_body
    assert "Convert to Python test" in py_body


def test_pipeline_editor_shows_live_row_counts(client):
    _seed_terminated_access(client)
    _make_control(client, "C1")
    _save_pipeline(client, "C1", _terminated_access_graph())
    body = client.get("/controls/C1/pipeline").text
    # 4 accounts → 3 active → join 2 → test 2. The counts narrow at each joint.
    assert "rows: <strong>4</strong>" in body      # acc import
    assert "rows: <strong>3</strong>" in body      # active filter
    assert "rows: <strong>2</strong>" in body      # join / test


def test_diagram_lays_join_branches_in_separate_converging_lanes():
    """The multi-lane view-model puts a Join's two feeder branches in distinct
    lanes that converge at the Join, while keeping every edge a real input→node
    relationship (presentation-only — never reorders execution)."""
    from controlflow_sdk.pipeline.model import parse_pipeline
    from controlflow_sdk.plane.routes.pipeline import _diagram

    pipeline = parse_pipeline(_terminated_access_graph())
    diagram = _diagram(pipeline, counts={})

    lane = {b["id"]: b["lane"] for b in diagram["boxes"]}
    row = {b["id"]: b["row"] for b in diagram["boxes"]}

    # Two lanes: the spine (acc→active→join→tst) and the employee branch.
    assert diagram["lanes"] == 2
    assert lane["acc"] == lane["active"] == lane["join"] == lane["tst"] == 0
    assert lane["emp"] == lane["term"] == 1
    # The terminal sits on the spine lane.
    assert next(b for b in diagram["boxes"] if b["terminal"])["lane"] == 0

    # Edges exactly mirror the graph's input→node relationships (no spurious or
    # missing edges), and each carries the right (lane, row) for both endpoints.
    by_id = {n.id: n for n in pipeline.nodes}
    expected = {
        (row[src], row[nid])
        for nid, n in by_id.items()
        for src in n.inputs
    }
    actual = {(e["from_row"], e["to_row"]) for e in diagram["edges"]}
    assert actual == expected
    # The Join fan-in converges across lanes: term (lane 1) → join (lane 0).
    term_to_join = next(
        e for e in diagram["edges"]
        if e["from_row"] == row["term"] and e["to_row"] == row["join"]
    )
    assert term_to_join["from_lane"] == 1 and term_to_join["to_lane"] == 0
    # The spine edges stay in-lane (straight vertical), e.g. active → join.
    active_to_join = next(
        e for e in diagram["edges"]
        if e["from_row"] == row["active"] and e["to_row"] == row["join"]
    )
    assert active_to_join["from_lane"] == active_to_join["to_lane"] == 0


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

    resp = client.post("/controls/C1/logic/convert", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert resp.headers["location"] == "/controls/C1/logic/python"

    from controlflow_sdk.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "C1")
    conn.close()
    # The one-way door: kind becomes python, code is the stitched test(), graph dropped.
    assert c["test_kind"] == "python"
    assert c["test_code"] is not None and "def test(pop, sources):" in c["test_code"]
    assert c["pipeline"] is None
    # The escape-hatch editor renders the prefilled code on the Python tab.
    body = client.get("/controls/C1/logic/python").text
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

    client.post("/controls/C3/logic/convert", follow_redirects=False)
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
    """GET /controls/{id}/logic/builder resolves to the editor, not the definition
    catch-all (learning 0007 route-ordering)."""
    routes = [r for r in client.app.router.routes
              if getattr(r, "path", "") == "/controls/{control_id}/logic/builder"
              and "GET" in getattr(r, "methods", set())]
    assert routes, "logic/builder GET sub-route is registered"
    paths = [getattr(r, "path", "") for r in client.app.router.routes]
    assert paths.index("/controls/{control_id}/logic/builder") < paths.index(
        "/controls/{control_id}"
    ), "logic/builder sub-route must precede the /{control_id} catch-all"


# ---------------------------------------------------------------------------
# Task 3: Logic sub-routes + tab nav
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded_pipeline_control(client):
    """A control with a minimal 2-node pipeline saved; returns the control id."""
    _make_source(client, "sp_accounts", b"account_id,is_active\nA1,true\nA2,false\n")
    cid = "SP1"
    _make_control(client, cid)
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "sp_accounts"},
        {"id": "tst", "type": "test", "inputs": ["imp"],
         "config": {"logic": "all", "severity": "high", "item_key_column": "account_id",
                    "description_template": "Active {account_id}",
                    "conditions": [{"column": "is_active", "op": "eq", "value": "true"}]}},
    ]}
    r = client.post(f"/controls/{cid}/logic/builder",
                    data={"pipeline_json": json.dumps(graph)},
                    follow_redirects=False)
    assert r.status_code in (302, 303, 307), f"save failed: {r.status_code}"
    return cid


def test_logic_subroutes_render(client, seeded_pipeline_control):
    cid = seeded_pipeline_control
    for sub in ("builder", "flowchart", "python"):
        r = client.get(f"/controls/{cid}/logic/{sub}")
        assert r.status_code == 200, f"/logic/{sub} returned {r.status_code}"
        assert 'class="tab active"' in r.text, f"/logic/{sub}: no active sub-tab"


def test_logic_bare_redirects_to_builder(client, seeded_pipeline_control):
    r = client.get(f"/controls/{seeded_pipeline_control}/logic", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"].endswith("/logic/builder")


def test_old_pipeline_url_redirects(client, seeded_pipeline_control):
    r = client.get(f"/controls/{seeded_pipeline_control}/pipeline", follow_redirects=False)
    assert r.status_code in (301, 308)
    assert r.headers["location"].endswith("/logic/builder")


def test_control_tab_says_logic_not_pipeline(client, seeded_pipeline_control):
    r = client.get(f"/controls/{seeded_pipeline_control}/logic/builder")
    assert ">Logic<" in r.text and ">Pipeline<" not in r.text


# ---------------------------------------------------------------------------
# Task 4: Split Builder / Flowchart panes; Builder derives graph
# ---------------------------------------------------------------------------

def _make_rule_control(client) -> str:
    """Create a control with a bound source (no pipeline/rule_spec yet).

    The Definition form no longer processes test_kind/rule_spec; controls start
    with empty logic (test_kind="pipeline").  The Builder derives an Import→Test
    scaffold from the bound source on first view, so tests that probe the Builder
    output can rely on those derived nodes being present.
    """
    _make_source(client, "rc_accounts", b"account_id,is_active\nA1,true\nA2,false\n")
    cid = "RC1"
    client.post("/controls", data={
        "id": cid, "title": "Rule Control", "objective": "o", "narrative": "n",
        "source_ids": "rc_accounts",
    }, follow_redirects=False)
    return cid


def _make_raw_python_control(client) -> str:
    """Create a control with hand-written test_code and no pipeline/rule_spec.

    The Definition form no longer accepts test_code; raw-Python controls are
    authored on the Logic ▸ Python tab (POST /controls/{id}/logic/python).
    """
    _make_source(client, "rp_accounts", b"account_id,amount\nA1,100\n")
    cid = "RP1"
    # 1. Create the metadata shell via the Definition form.
    client.post("/controls", data={
        "id": cid, "title": "Raw Python Control", "objective": "o", "narrative": "n",
        "source_ids": "rp_accounts",
    }, follow_redirects=False)
    # 2. Write the hand-authored test_code via the Logic ▸ Python route.
    client.post(f"/controls/{cid}/logic/python",
                data={"test_code": "def test(pop, sources):\n    return []\n"},
                follow_redirects=False)
    return cid


def test_builder_shows_nodes_for_rule_control(client):
    cid = _make_rule_control(client)
    r = client.get(f"/controls/{cid}/logic/builder")
    assert r.status_code == 200
    # Assert the DERIVED node cards actually rendered (data-type attribute on the
    # card elements), not just text that also appears in the toolbar buttons.
    assert 'data-type="import"' in r.text, "derived Import node card missing"
    assert 'data-type="test"' in r.text, "derived Test node card missing"
    assert "Generated Python" not in r.text                 # python moved to its own tab


def test_builder_derives_graph_for_rule_control_and_save_persists(client):
    """Regression: opening Builder on a rule_spec control, then POSTing the derived
    graph must persist a pipeline (not silently discard it).  Before the fix the
    hidden pipeline_json was initialised from the empty stored graph so the derived
    scaffold nodes were never submitted."""
    cid = _make_rule_control(client)

    # 1. GET the builder — it renders derived Import→Test nodes.
    r = client.get(f"/controls/{cid}/logic/builder")
    assert r.status_code == 200
    assert 'data-type="import"' in r.text and 'data-type="test"' in r.text

    # 2. Extract the derived graph from the embedded JSON blob (what the JS would
    #    read and submit on form submit / node-add).
    import re as _re
    m = _re.search(
        r'<script id="graph-data"[^>]*>(.*?)</script>', r.text, _re.DOTALL
    )
    assert m, "graph-data script tag not found in builder HTML"
    derived_graph = json.loads(m.group(1).strip())
    assert derived_graph.get("nodes"), "derived graph has no nodes"
    # The derived graph must have an import and a test node (not be empty).
    node_types = {n["type"] for n in derived_graph["nodes"]}
    assert "import" in node_types and "test" in node_types

    # 3. POST that graph as the browser JS would after serialising the DOM cards.
    resp = _save_pipeline(client, cid, derived_graph)
    assert resp.status_code in (302, 303), f"save returned {resp.status_code}"

    # 4. The control must now have a persisted pipeline (not None).
    from controlflow_sdk.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, cid)
    conn.close()
    assert c["pipeline"] is not None, "pipeline was not persisted after save"
    # And it must still compile to a rule_spec or test_code (cardinal rule 0001).
    assert c["rule_spec"] is not None or c["test_code"] is not None, (
        "control has a pipeline but neither rule_spec nor test_code — bundle broken"
    )


def test_flowchart_tab_has_svg_only(client, seeded_pipeline_control):
    r = client.get(f"/controls/{seeded_pipeline_control}/logic/flowchart")
    assert r.status_code == 200
    assert "<svg" in r.text
    assert "+ Import" not in r.text                         # no builder toolbar here


def test_builder_shows_python_notice_for_raw_python(client):
    cid = _make_raw_python_control(client)
    r = client.get(f"/controls/{cid}/logic/builder")
    assert r.status_code == 200
    assert "authored directly in Python" in r.text


# ---------------------------------------------------------------------------
# Task 5: Logic ▸ Python tab — generated view + relocated escape hatch
# ---------------------------------------------------------------------------

def test_python_tab_readonly_generated_for_graph_control(client, seeded_pipeline_control):
    r = client.get(f"/controls/{seeded_pipeline_control}/logic/python")
    assert "def test(" in r.text
    assert "Convert to Python test" in r.text


def test_python_tab_editable_for_raw_python(client):
    cid = _make_raw_python_control(client)
    r = client.get(f"/controls/{cid}/logic/python")
    assert 'name="test_code"' in r.text                      # editable textarea present
    # save edits
    client.post(f"/controls/{cid}/logic/python",
                data={"test_code": "def test(pop):\n    return []"}, follow_redirects=False)


# ---------------------------------------------------------------------------
# Task 7: cross-source (not_exists_in) condition round-trips through Builder
# ---------------------------------------------------------------------------

_XSRC_GRAPH = {
    "nodes": [
        {"id": "imp", "type": "import", "source_id": "cs_accounts", "narrative": ""},
        {"id": "tst", "type": "test", "inputs": ["imp"], "narrative": "",
         "config": {
             "logic": "all",
             "severity": "high",
             "item_key_column": "account_id",
             "description_template": "Account {account_id} has no matching employee",
             "conditions": [{
                 "column": "employee_id",
                 "op": "not_exists_in",
                 "other_source": "cs_employees",
                 "this_key": "employee_id",
                 "other_key": "employee_id",
             }],
         }},
    ]
}


def _seed_cross_source(client):
    _make_source(client, "cs_accounts",
                 b"account_id,employee_id\nA1,E1\nA2,E2\nA3,E3\n")
    _make_source(client, "cs_employees",
                 b"employee_id,status\nE1,active\nE2,terminated\n")
    _make_control(client, "CS1")


def test_cross_source_condition_preserved_through_builder_save(client):
    """Regression guard: a Test node condition with op=not_exists_in must survive
    a Builder save round-trip without silent loss of other_source/this_key/other_key.

    Two-part check:
    1. The embedded graph-data JSON (used to initialise the JS graph state) carries
       the full cross-source condition — so the hidden field is initialised correctly.
    2. The rendered node-card HTML contains the cross-source DOM elements
       (data-xsrc-source, data-xsrc-this, data-xsrc-other) and the op is selected
       as "not_exists_in" — so the JS serialize() can read them back and a second
       Save (simulated by re-posting the embedded graph) preserves the condition.
    """
    _seed_cross_source(client)

    r = _save_pipeline(client, "CS1", _XSRC_GRAPH)
    assert r.status_code in (302, 303), f"save failed: {r.status_code}"

    # --- Check 1: embedded graph-data JSON carries the full condition -----------
    builder_r = client.get("/controls/CS1/logic/builder")
    assert builder_r.status_code == 200
    html = builder_r.text

    m = re.search(r'<script id="graph-data"[^>]*>(.*?)</script>', html, re.DOTALL)
    assert m, "graph-data script tag not found in builder HTML"
    embedded_graph = json.loads(m.group(1).strip())
    test_nodes = [n for n in embedded_graph.get("nodes", []) if n.get("type") == "test"]
    assert test_nodes, "no Test node in embedded graph"
    conditions = test_nodes[0].get("config", {}).get("conditions", [])
    assert conditions, "Test node has no conditions in embedded graph"
    cond = conditions[0]
    assert cond.get("op") == "not_exists_in", (
        f"op={cond.get('op')!r} in embedded JSON — cross-source op dropped"
    )
    assert cond.get("other_source") == "cs_employees", (
        f"other_source={cond.get('other_source')!r} in embedded JSON — dropped"
    )
    assert cond.get("this_key") == "employee_id", (
        f"this_key={cond.get('this_key')!r} in embedded JSON — dropped"
    )
    assert cond.get("other_key") == "employee_id", (
        f"other_key={cond.get('other_key')!r} in embedded JSON — dropped"
    )

    # --- Check 2: rendered DOM card has cross-source elements ------------------
    # The op-select for the condition must include not_exists_in as the selected option.
    assert 'value="not_exists_in" selected' in html, (
        "not_exists_in option not selected in condition op-select — "
        "JS serialize() would read the wrong op (e.g. 'eq') and silently drop the "
        "cross-source condition on Save"
    )
    # The cross-source input widgets must be present in the HTML so JS can read them.
    assert "data-xsrc-source" in html, (
        "data-xsrc-source element missing from rendered HTML — "
        "JS serialize() cannot read other_source on Save"
    )
    assert "data-xsrc-this" in html, (
        "data-xsrc-this element missing from rendered HTML — "
        "JS serialize() cannot read this_key on Save"
    )
    assert "data-xsrc-other" in html, (
        "data-xsrc-other element missing from rendered HTML — "
        "JS serialize() cannot read other_key on Save"
    )

    # --- Check 3: re-save the embedded graph (simulates JS Save click) ---------
    # The embedded graph is what the JS would submit; saving it must keep the condition.
    r2 = _save_pipeline(client, "CS1", embedded_graph)
    assert r2.status_code in (302, 303), f"re-save returned {r2.status_code}"

    from controlflow_sdk.store import repo
    conn = _conn(client)
    ctrl = repo.get_control(conn, "CS1")
    conn.close()
    pipeline = ctrl.get("pipeline") or {}
    saved_nodes = pipeline.get("nodes", [])
    saved_test = next((n for n in saved_nodes if n.get("type") == "test"), None)
    assert saved_test, "Test node missing after re-save"
    saved_conds = saved_test.get("config", {}).get("conditions", [])
    assert saved_conds, "conditions dropped after re-save"
    saved_cond = saved_conds[0]
    assert saved_cond.get("op") == "not_exists_in", (
        f"op={saved_cond.get('op')!r} after re-save — silently changed"
    )
    assert saved_cond.get("other_source") == "cs_employees", (
        f"other_source={saved_cond.get('other_source')!r} after re-save — dropped"
    )
    assert saved_cond.get("this_key") == "employee_id", (
        f"this_key={saved_cond.get('this_key')!r} after re-save — dropped"
    )
    assert saved_cond.get("other_key") == "employee_id", (
        f"other_key={saved_cond.get('other_key')!r} after re-save — dropped"
    )


# ---------------------------------------------------------------------------
# Task 9: incomplete Test-node condition must NOT 500 the Builder GET
# ---------------------------------------------------------------------------

def test_builder_degrades_gracefully_on_incomplete_test_condition(client):
    """GET /controls/{id}/logic/builder must return 200 (not 500) when the stored
    pipeline graph has a Test node with an incomplete condition (column="").

    Root cause: _row_counts() calls compute_row_counts() which parses the rule
    spec and raises RuleSpecError on an empty column — but _row_counts only caught
    RowCountError.  Row counts are non-critical preview; an incomplete in-progress
    graph must degrade to empty counts (template shows "—"), not 500.
    """
    # Persist a control with an INCOMPLETE Test node (column="" is invalid).
    _make_source(client, "inc_accounts", b"account_id,status\nA1,active\n")
    _make_control(client, "INC1")

    # Construct the stored graph directly via repo so the save path's validation
    # is bypassed (the save route would reject it; we need the GET to survive it
    # when the graph is already in that state — e.g. after a partial migration).
    from controlflow_sdk.store import repo
    conn = _conn(client)
    ctrl = repo.get_control(conn, "INC1")
    incomplete_graph = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "inc_accounts", "narrative": ""},
            {"id": "tst", "type": "test", "inputs": ["imp"], "narrative": "",
             "config": {
                 "logic": "all",
                 "severity": "high",
                 "item_key_column": "account_id",
                 "description_template": "Account {account_id}",
                 # INCOMPLETE condition: column="" triggers RuleSpecError in parse_rule_spec
                 "conditions": [{"column": "", "op": "eq", "value": "active"}],
             }},
        ]
    }
    repo.upsert_control(
        conn,
        id=ctrl["id"],
        title=ctrl["title"],
        objective=ctrl["objective"],
        narrative=ctrl["narrative"],
        framework_refs=ctrl["framework_refs"],
        test_kind="pipeline",
        rule_spec=None,
        test_code=None,
        pipeline=incomplete_graph,
        failure_threshold_pct=ctrl["failure_threshold_pct"],
        failure_threshold_count=ctrl["failure_threshold_count"],
    )
    conn.close()

    # The builder GET must return 200 (not 500) despite the broken condition.
    r = client.get("/controls/INC1/logic/builder")
    assert r.status_code == 200, (
        f"Expected 200 but got {r.status_code} — incomplete Test-node condition 500s the editor"
    )
    # The node cards must still render (import + test nodes visible).
    assert "data-node=" in r.text, "node cards missing from builder response"


# ---------------------------------------------------------------------------
# F1: guard POST /controls/{id}/logic/python — must not clobber a GRAPH control
# ---------------------------------------------------------------------------

def test_python_save_does_not_clobber_graph_control(client):
    """A stray POST to /logic/python on a GRAPH control must be a no-op.

    If the control already has a pipeline or rule_spec (it is NOT raw-python),
    the save handler must redirect back without writing — preserving the existing
    pipeline and rule_spec.
    """
    _seed_terminated_access(client)
    _make_control(client, "G1")
    _save_pipeline(client, "G1", _terminated_access_graph())

    from controlflow_sdk.store import repo
    conn = _conn(client)
    before = repo.get_control(conn, "G1")
    conn.close()
    assert before["pipeline"] is not None, "setup: control must have a pipeline"
    assert before["rule_spec"] is None or before["test_code"] is not None or before["pipeline"]

    # Stray POST to /logic/python — should be a no-op for a graph control.
    resp = client.post("/controls/G1/logic/python",
                       data={"test_code": "def test(pop, sources):\n    return []\n"},
                       follow_redirects=False)
    assert resp.status_code in (302, 303), f"expected redirect, got {resp.status_code}"

    conn = _conn(client)
    after = repo.get_control(conn, "G1")
    conn.close()
    # The pipeline must be unchanged.
    assert after["pipeline"] == before["pipeline"], "pipeline was clobbered by /logic/python POST"
    # test_code must NOT have been set to the posted value.
    assert after["test_code"] != "def test(pop, sources):\n    return []\n", (
        "test_code was overwritten on a graph control by /logic/python POST"
    )


def test_python_save_works_for_raw_python_control(client):
    """Regression: POST /logic/python must still save for a genuinely raw-python control."""
    cid = _make_raw_python_control(client)

    new_code = "def test(pop, sources):\n    return list(pop.df.itertuples())\n"
    resp = client.post(f"/controls/{cid}/logic/python",
                       data={"test_code": new_code},
                       follow_redirects=False)
    assert resp.status_code in (302, 303), f"expected redirect, got {resp.status_code}"

    from controlflow_sdk.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, cid)
    conn.close()
    assert c["test_code"] == new_code, "test_code was not saved for a raw-python control"
    assert c["pipeline"] is None
    assert c["rule_spec"] is None


# ---------------------------------------------------------------------------
# Task 7: Bundle — N procedures for forked controls
# ---------------------------------------------------------------------------

def _seed_forked_bundle_control(client):
    """Seed a forked 2-terminal pipeline control, run it, return the control id.

    Two terminal Test nodes share one Import:
    - Terminal "a" ("High-value items"):  flags items with category == "high"
    - Terminal "b" ("Low-value items"):   flags items with category == "low"
    """
    _make_source(client, "bundle_inv",
                 b"item_id,category\nI1,low\nI2,low\nI3,high\nI4,normal\n")
    cid = "FORK1"
    _make_control(client, cid)

    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "bundle_inv"},
        {
            "id": "a", "type": "test", "inputs": ["imp"],
            "config": {
                "logic": "all",
                "conditions": [{"column": "category", "op": "eq", "value": "high"}],
                "severity": "high",
                "description_template": "Item {item_id} is high-value",
                "item_key_column": "item_id",
                "title": "High-value items",
            },
        },
        {
            "id": "b", "type": "test", "inputs": ["imp"],
            "config": {
                "logic": "all",
                "conditions": [{"column": "category", "op": "eq", "value": "low"}],
                "severity": "medium",
                "description_template": "Item {item_id} is low-value",
                "item_key_column": "item_id",
                "title": "Low-value items",
            },
        },
    ]}
    r = _save_pipeline(client, cid, graph)
    assert r.status_code in (302, 303), f"save pipeline failed: {r.status_code}"
    r2 = client.post(f"/controls/{cid}/run", follow_redirects=False)
    assert r2.status_code in (302, 303), f"run failed: {r2.status_code}"
    return cid


def test_forked_control_bundle_has_n_procedures(client):
    """A forked 2-terminal pipeline control must export with 2 workpaper procedures.

    Asserts (per task-7-brief):
    - manifest["schema_version"] == "1.0"
    - validate_bundle(manifest) == []           (contract gate — schema 1.0 intact)
    - workpaper["procedures"] has length 2
    - The two procedure titles are distinct
    - control["test_code"] is non-empty (the union test())
    """
    from controlflow_sdk.schema.validate import validate_bundle

    _seed_forked_bundle_control(client)

    resp = client.post("/export", follow_redirects=False)
    assert resp.status_code == 200, f"export failed: {resp.status_code}"

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))

    assert manifest["schema_version"] == "1.0"
    assert validate_bundle(manifest) == [], f"schema errors: {validate_bundle(manifest)}"

    ctrl = next(c for c in manifest["controls"] if c["id"] == "FORK1")

    # The union test() must be non-empty and be Python (it concatenates branches).
    assert ctrl["test_code"], "control test_code is empty — union test() missing"
    assert "def test(" in ctrl["test_code"], "test_code does not look like a test() function"

    procs = ctrl["workpaper"]["procedures"]
    assert len(procs) == 2, (
        f"expected 2 workpaper procedures for a forked control, got {len(procs)}. "
        f"Titles: {[p.get('title') for p in procs]}"
    )

    titles = [p["title"] for p in procs]
    assert len(set(titles)) == 2, f"procedure titles are not distinct: {titles}"

    # Each procedure must have non-empty test_code and a result.
    for p in procs:
        assert p["test_code"], f"procedure {p['title']!r}: test_code is empty"
        assert "result" in p, f"procedure {p['title']!r}: missing result"


# ---------------------------------------------------------------------------
# Task 8: Builder UI — Test-card fields + relaxed save + all-terminals
# ---------------------------------------------------------------------------

def _forked_graph_with_titles() -> dict:
    """A forked 2-terminal graph where each Test node has config.title + thresholds."""
    return {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "fork_src", "narrative": ""},
            {
                "id": "a", "type": "test", "inputs": ["imp"], "narrative": "",
                "config": {
                    "logic": "all",
                    "severity": "high",
                    "item_key_column": "item_id",
                    "description_template": "Item {item_id} high",
                    "conditions": [{"column": "category", "op": "eq", "value": "high"}],
                    "title": "High-value items",
                    "failure_threshold_pct": 5.0,
                    "failure_threshold_count": 10,
                },
            },
            {
                "id": "b", "type": "test", "inputs": ["imp"], "narrative": "",
                "config": {
                    "logic": "all",
                    "severity": "medium",
                    "item_key_column": "item_id",
                    "description_template": "Item {item_id} low",
                    "conditions": [{"column": "category", "op": "eq", "value": "low"}],
                    "title": "Low-value items",
                    "failure_threshold_pct": 2.5,
                    "failure_threshold_count": 0,
                },
            },
        ]
    }


def _seed_forked_t8(client):
    _make_source(client, "fork_src",
                 b"item_id,category\nI1,low\nI2,high\nI3,normal\n")
    _make_control(client, "T8")


def test_forked_builder_renders_both_test_cards_with_proc_title_and_threshold_fields(client):
    """GET /controls/T8/logic/builder for a forked 2-terminal control must:
    - render BOTH Test cards (data-node="a" and data-node="b")
    - each Test card must carry data-proc-title, data-threshold-pct, data-threshold-count inputs
    """
    _seed_forked_t8(client)
    graph = _forked_graph_with_titles()
    r = _save_pipeline(client, "T8", graph)
    assert r.status_code in (302, 303), f"save failed: {r.status_code}"

    builder_r = client.get("/controls/T8/logic/builder")
    assert builder_r.status_code == 200
    html = builder_r.text

    # Both Test cards rendered.
    assert 'data-node="a"' in html, "Test card 'a' not found in builder"
    assert 'data-node="b"' in html, "Test card 'b' not found in builder"

    # Both cards have the new procedure-title and threshold inputs.
    assert "data-proc-title" in html, "data-proc-title attribute missing from Test card"
    assert "data-threshold-pct" in html, "data-threshold-pct attribute missing from Test card"
    assert "data-threshold-count" in html, "data-threshold-count attribute missing from Test card"

    # Saved titles round-trip back into the rendered value attributes.
    assert 'value="High-value items"' in html, "Procedure title 'High-value items' not rendered"
    assert 'value="Low-value items"' in html, "Procedure title 'Low-value items' not rendered"


def test_forked_builder_post_saves_and_roundtrips_titles(client):
    """POST a forked graph with config.title + thresholds → 303 (not 422);
    GET back → both titles visible in builder HTML."""
    _seed_forked_t8(client)
    graph = _forked_graph_with_titles()

    r = _save_pipeline(client, "T8", graph)
    assert r.status_code in (302, 303), f"expected 303, got {r.status_code}: {r.text[:400]}"

    html = client.get("/controls/T8/logic/builder").text
    assert "High-value items" in html, "Title 'High-value items' missing after save"
    assert "Low-value items" in html, "Title 'Low-value items' missing after save"


def test_diagram_marks_all_terminals_for_forked_control():
    """_diagram must mark BOTH terminal Test boxes terminal=True for a forked pipeline.

    Before the fix _diagram used ``n.id == pipeline.terminal.id`` (only terminals[0]).
    After the fix it uses ``n.id in {t.id for t in pipeline.terminals}`` so ALL
    terminal boxes are marked.
    """
    from controlflow_sdk.pipeline.model import parse_pipeline
    from controlflow_sdk.plane.routes.pipeline import _diagram

    graph = _forked_graph_with_titles()
    pipeline = parse_pipeline(graph)

    # Sanity: both "a" and "b" are terminals.
    assert len(pipeline.terminals) == 2
    terminal_ids = {t.id for t in pipeline.terminals}
    assert "a" in terminal_ids and "b" in terminal_ids

    diagram = _diagram(pipeline, counts={})
    terminal_boxes = [b for b in diagram["boxes"] if b["terminal"]]
    terminal_box_ids = {b["id"] for b in terminal_boxes}

    assert len(terminal_boxes) == 2, (
        f"expected 2 terminal boxes, got {len(terminal_boxes)}: {terminal_box_ids}"
    )
    assert terminal_box_ids == {"a", "b"}, (
        f"wrong terminal boxes: {terminal_box_ids}"
    )


def test_single_terminal_back_compat(client):
    """Single-terminal controls still work after the Task 8 changes.

    The new title/threshold inputs appear but are empty; saving without them
    must succeed (no 422), and the pipeline persists correctly.
    """
    _make_source(client, "bt_items", b"item_id,flag\nI1,yes\nI2,no\n")
    _make_control(client, "BT1")
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "bt_items"},
        {"id": "tst", "type": "test", "inputs": ["imp"],
         "config": {"logic": "all", "severity": "high", "item_key_column": "item_id",
                    "description_template": "Item {item_id}",
                    "conditions": [{"column": "flag", "op": "eq", "value": "yes"}]}},
    ]}
    r = _save_pipeline(client, "BT1", graph)
    assert r.status_code in (302, 303), f"single-terminal save failed: {r.status_code}"

    # The builder must render with the new inputs present (but empty).
    html = client.get("/controls/BT1/logic/builder").text
    assert "data-proc-title" in html, "data-proc-title missing on single-terminal card"
    assert "data-threshold-pct" in html, "data-threshold-pct missing on single-terminal card"
    assert "data-threshold-count" in html, "data-threshold-count missing on single-terminal card"

    # The flowchart marks the single terminal correctly.
    from controlflow_sdk.pipeline.model import parse_pipeline
    from controlflow_sdk.plane.routes.pipeline import _diagram
    pipeline = parse_pipeline(graph)
    diagram = _diagram(pipeline, counts={})
    terminal_boxes = [b for b in diagram["boxes"] if b["terminal"]]
    assert len(terminal_boxes) == 1 and terminal_boxes[0]["id"] == "tst"
# --- Logic UX polish: flowchart narrative + per-gap insert affordances -------

def test_diagram_boxes_carry_node_narrative():
    """Each flowchart box exposes its node's narrative so the SVG can show the
    beginning of it (truncated) with the full text on hover."""
    from controlflow_sdk.pipeline.model import parse_pipeline
    from controlflow_sdk.plane.routes.pipeline import _diagram

    diagram = _diagram(parse_pipeline(_terminated_access_graph()), counts={})
    narr = {b["id"]: b["narrative"] for b in diagram["boxes"]}
    assert narr["acc"] == "All access accounts"
    assert narr["join"] == "Active accounts of terminated employees"
    # A node with no narrative carries an empty string (never KeyErrors / None).
    assert narr["tst"] == ""


def test_flowchart_shows_narrative_truncated_with_full_text_on_hover(client):
    _seed_terminated_access(client)
    _make_control(client, "C1")
    long_narr = (
        "Keep only posted invoices above the materiality threshold so immaterial "
        "noise is excluded from the exception population auditors review"
    )
    graph = {"nodes": [
        {"id": "src", "type": "import", "source_id": "access_accounts",
         "narrative": long_narr},
        {"id": "tst", "type": "test", "inputs": ["src"], "narrative": "Short note",
         "config": {"logic": "all", "severity": "low",
                    "conditions": [{"column": "account_id", "op": "not_empty"}]}},
    ]}
    assert _save_pipeline(client, "C1", graph).status_code in (302, 303)

    body = client.get("/controls/C1/logic/flowchart").text
    # Full narrative is available on hover via a <title> element.
    assert f"<title>{long_narr}</title>" in body
    # The visible label is truncated with an ellipsis (the author sees the start).
    assert 'class="fc-narr"' in body
    assert "…" in body
    # The beginning of the long narrative is shown inline …
    assert "Keep only posted invoices" in body
    # … but the full long text never appears as a visible <text> run (only in <title>).
    assert f'class="fc-narr">{long_narr}<' not in body
    # A short narrative is shown verbatim (no truncation).
    assert "Short note" in body


def test_builder_has_per_gap_insert_affordances_not_only_bottom_toolbar(client):
    """Steps can be inserted at any position: an insert control sits at every gap
    (top, between each pair of cards, bottom), each carrying the up/down node ids
    it splices between — replacing the old single bottom-only add toolbar."""
    _seed_terminated_access(client)
    _make_control(client, "C1")
    assert _save_pipeline(client, "C1", _terminated_access_graph()).status_code in (302, 303)

    body = client.get("/controls/C1/logic/builder").text
    # The old single bottom toolbar is gone.
    assert 'class="pipe-toolbar"' not in body
    # Insert affordances exist, positioned by up/down ids.
    assert "data-insert-toggle" in body
    assert "data-insert" in body
    # One insert zone per gap: N cards → N+1 zones (top + between + bottom).
    # Count zone elements by class (the substring "data-insert-toggle" also
    # appears once in the page's delegation JS, so don't substring-count that).
    n_nodes = len(_terminated_access_graph()["nodes"])
    assert len(re.findall(r'class="pipe-insert pipe-insert-', body)) == n_nodes + 1
    # Each node type is offerable from an insert menu.
    for t in ("import", "filter", "join", "custom_python", "test"):
        assert f'data-type="{t}"' in body


# ---------------------------------------------------------------------------
# Task 2: autosave — in-place card swaps for routine edits
# ---------------------------------------------------------------------------

def test_builder_renders_autosave_status_affordance(client):
    """The builder page contains the autosave status element and the
    autosaveSubmit JS helper so that add/remove/insert actions swap only
    the #pipe-cards region without a full-page reload."""
    _make_control(client, "C1")
    body = client.get("/controls/C1/logic/builder").text

    # The status affordance element must be present.
    assert 'id="autosave-status"' in body, "autosave-status element missing"

    # The JS helper that issues the autosave fetch must be defined.
    assert "autosaveSubmit" in body, "autosaveSubmit helper missing"

    # Full-page form.submit() must NOT be used for card-mutation actions.
    # The only legitimate submit path is the explicit Save pipeline button,
    # which triggers the form's own submit event — not a programmatic .submit().
    assert "pipeline-form').submit()" not in body, (
        "found full-page .submit() in card-mutation handlers — "
        "card mutations should call autosaveSubmit()"
    )


def test_autosave_post_returns_cards_fragment_not_redirect(client):
    """POST /controls/{id}/logic/builder with autosave=1 returns the
    pipe-cards HTML fragment (200) instead of a redirect, so the client-side
    fetch can swap only #pipe-cards without a full-page reload."""
    _make_source(client, "accounts", b"account_id,is_active\nA1,true\nA2,false\n")
    _make_control(client, "C1")
    # A minimal valid pipeline: Import → Test (terminal).
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "accounts",
         "narrative": "", "config": {}, "inputs": []},
        {"id": "tst", "type": "test", "inputs": ["imp"],
         "narrative": "", "config": {"logic": "all", "conditions": []}},
    ]}
    resp = client.post(
        "/controls/C1/logic/builder",
        data={"pipeline_json": json.dumps(graph), "autosave": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    # Fragment must contain the node cards — not a full page.
    assert 'data-node="imp"' in resp.text
    assert 'data-node="tst"' in resp.text
    assert "<html" not in resp.text


def test_autosave_validation_error_returns_cards_fragment_not_full_page(client):
    """When autosave=1 and a lint error fires, the server MUST return a 422
    cards fragment (not the full logic_builder.html page).

    The newly submitted node must remain visible in the fragment with the
    error surfaced inline — so the author can see and fix it without losing
    the node they just inserted.  (Issue: full-page 422 drops the DOM node.)
    """
    _make_source(client, "je_frag", b"entry_id,amount\nE1,100\n")
    _make_control(client, "ErrFrag")
    bad_graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "je_frag",
         "inputs": [], "config": {}},
        {"id": "cust", "type": "custom_python", "inputs": ["imp"],
         "config": {"flavor": "transform",
                    "code": "rows = open('/etc/passwd').read()"}},
        {"id": "tst", "type": "test", "inputs": ["cust"],
         "config": {"logic": "all", "conditions": [],
                    "item_key_column": "entry_id"}},
    ]}
    resp = client.post(
        "/controls/ErrFrag/logic/builder",
        data={"pipeline_json": json.dumps(bad_graph), "autosave": "1"},
        follow_redirects=False,
    )
    # Must still signal an error (422), not a success or redirect.
    assert resp.status_code == 422, (
        f"autosave error must return 422, got {resp.status_code}"
    )
    # The response MUST be a fragment, not a full HTML page.
    assert "<html" not in resp.text, (
        "autosave error should return a cards fragment, not the full HTML page"
    )
    # All submitted nodes must remain visible so the author can fix the error.
    assert 'data-node="imp"' in resp.text
    assert 'data-node="cust"' in resp.text
    # The lint error must be surfaced inside the fragment.
    assert _OFFRAMP_STABLE in resp.text, (
        "lint error must appear in the autosave error fragment"
    )


def test_autosave_js_updates_cards_on_non_200_response(client):
    """The autosaveSubmit JS must update #pipe-cards even when the server
    returns a non-200 status (e.g. 422 validation error), so the newly
    inserted node stays visible and can be configured by the author.

    The old ``if (!resp.ok) { throw`` pattern discards the response body on
    any non-2xx status, which causes the browser to silently drop the node.
    """
    _make_control(client, "JS1")
    body = client.get("/controls/JS1/logic/builder").text
    # The throw-on-non-OK guard must be absent.
    assert "if (!resp.ok) { throw" not in body, (
        "autosaveSubmit must not throw on non-OK status; "
        "non-200 responses must still update #pipe-cards"
    )


def test_autosave_js_sequence_guard_present(client):
    """The autosaveSubmit JS must include a sequence counter guard so only
    the newest in-flight autosave response is applied to #pipe-cards.

    Without the guard, two rapid autosave requests can resolve out of order
    and the older (stale) response clobbers the newer one.
    """
    _make_control(client, "JS2")
    body = client.get("/controls/JS2/logic/builder").text
    assert "_autosaveSeq" in body, (
        "autosaveSubmit must define a sequence counter (_autosaveSeq) "
        "to prevent concurrent autosave responses from clobbering the DOM"
    )
    assert "seq !== _autosaveSeq" in body, (
        "autosaveSubmit must check the sequence counter before applying "
        "a response (seq !== _autosaveSeq guard)"
    )


def test_builder_renders_recalculate_button(client):
    """The builder page contains a Recalculate button that allows users to
    refresh row counts after structural changes (e.g., removing nodes).

    The button serializes the current graph and calls reload() to trigger
    an autosave, which recalculates row counts and updates the UI in place.
    """
    _make_control(client, "RC1")
    body = client.get("/controls/RC1/logic/builder").text

    # The recalculate button must be present with correct ID.
    assert 'id="recalc-btn"' in body, "recalc-btn element missing"

    # The button text must be visible.
    assert '↻ Recalculate' in body or 'Recalculate' in body, (
        "Recalculate button text missing"
    )

    # The button must have a click handler that calls reload().
    assert 'recalc-btn' in body and 'addEventListener' in body and 'reload()' in body, (
        "recalc-btn click handler missing or doesn't call reload()"
    )
