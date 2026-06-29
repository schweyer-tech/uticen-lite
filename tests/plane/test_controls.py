import io

from uticen_lite.store import repo
from uticen_lite.store.db import connect


def _make_source(client, sid="users"):
    csv = b"user_id,can_create,can_approve\nU1,true,false\n"
    client.post("/sources", data={"source_id": sid, "format": "csv"},
                files={"file": (f"{sid}.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)


def _get_control(client, cid: str) -> dict:
    """Read a control dict directly from the store."""
    conn = connect(client.app.state.project_root)
    try:
        return repo.get_control(conn, cid)
    finally:
        conn.close()


def _make_rule_control(client) -> str:
    """Create a control that has a rule_spec via the Logic Builder route."""
    _make_source(client, "rc_def_accounts")
    cid = "RCD1"
    # Create the control shell via the Definition form (metadata only now).
    client.post("/controls", data={
        "id": cid, "title": "Rule Control", "objective": "o", "narrative": "n",
        "source_ids": "rc_def_accounts",
    }, follow_redirects=False)
    # Save a rule_spec via the Logic Builder route so the control has logic.
    import json
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "rc_def_accounts", "narrative": ""},
        {"id": "tst", "type": "test", "inputs": ["imp"], "narrative": "",
         "config": {"logic": "all", "severity": "medium", "item_key_column": "user_id",
                    "description_template": "User {user_id} flagged",
                    "conditions": [{"column": "can_create", "op": "eq", "value": "true"}]}},
    ]}
    client.post(f"/controls/{cid}/logic/builder",
                data={"pipeline_json": json.dumps(graph)},
                follow_redirects=False)
    return cid


def test_create_control_redirects(client):
    """POST /controls creates a control and redirects (metadata only; logic is empty)."""
    _make_source(client)
    resp = client.post("/controls", data={
        "id": "meta1", "title": "Meta", "objective": "o", "narrative": "n",
        "framework_nist": "AC-2, AC-5",
        "source_ids": ["users"],
    }, follow_redirects=False)
    assert resp.status_code in (302, 303)
    c = _get_control(client, "meta1")
    assert c["test_kind"] == "pipeline"
    assert c["rule_spec"] is None
    assert c["test_code"] is None
    assert c["pipeline"] is None
    assert c["framework_refs"] == {"nist": ["AC-2", "AC-5"]}
    assert c["source_ids"] == ["users"]


def test_edit_control_shows_values(client):
    _make_source(client)
    client.post("/controls", data={
        "id": "py2", "title": "Editable", "objective": "o", "narrative": "n",
        "source_ids": ["users"]}, follow_redirects=False)
    page = client.get("/controls/py2")
    assert page.status_code == 200
    assert "Editable" in page.text


def test_edit_control_shows_id_in_details_box(client):
    # 2026-06-27 review: the control ID is a plain editable field in the
    # Definition "Details" box — like any other field, no separate Edit
    # button or rename form. Saving the form renames the control.
    _make_source(client)
    client.post("/controls", data={
        "id": "HDRID1", "title": "Header ID", "objective": "o", "narrative": "n",
        "source_ids": ["users"]}, follow_redirects=False)

    page = client.get("/controls/HDRID1")
    assert page.status_code == 200
    assert 'class="control-id-banner"' not in page.text   # gone from the header
    # A normal editable field inside the metadata form, valued at the current id.
    assert 'id="f-id"' in page.text
    assert 'name="id"' in page.text
    assert 'value="HDRID1"' in page.text
    # The standalone rename mechanism is gone: no separate Edit button/form.
    assert 'class="control-id-row"' not in page.text
    assert 'id="id-rename-form"' not in page.text
    assert 'name="new_id"' not in page.text
    assert 'action="/controls/HDRID1/id"' not in page.text


def test_edit_control_renames_via_main_save(client):
    # Editing the Control ID field and saving the Definition form renames the
    # control everywhere (sources, runs) — the same path as any field edit.
    _make_source(client)
    client.post("/controls", data={
        "id": "CIDOLD1", "title": "Rename me", "objective": "o", "narrative": "n",
        "source_ids": ["users"]}, follow_redirects=False)

    resp = client.post("/controls/CIDOLD1", data={
        "id": "CIDNEW1", "title": "Rename me", "objective": "o", "narrative": "n",
        "source_ids": ["users"]}, follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert resp.headers["location"] == "/controls/CIDNEW1"
    assert _get_control(client, "CIDOLD1") is None
    renamed = _get_control(client, "CIDNEW1")
    assert renamed is not None
    assert renamed["title"] == "Rename me"
    assert renamed["source_ids"] == ["users"]


def test_edit_control_rename_to_existing_id_is_friendly_not_500(client):
    # Renaming onto an id that already exists must surface a friendly error,
    # never a 500 from the rename's ValueError.
    _make_source(client)
    for cid in ("CIDA1", "CIDB1"):
        client.post("/controls", data={
            "id": cid, "title": cid, "objective": "o", "narrative": "n",
            "source_ids": ["users"]}, follow_redirects=False)
    resp = client.post("/controls/CIDA1", data={
        "id": "CIDB1", "title": "CIDA1", "objective": "o", "narrative": "n",
        "source_ids": ["users"]}, follow_redirects=False)
    assert resp.status_code != 500, resp.text
    assert "already exists" in resp.text
    # both originals still exist (the clashing rename was refused)
    assert _get_control(client, "CIDA1") is not None
    assert _get_control(client, "CIDB1") is not None


def test_edit_control_moves_title_editing_to_header(client):
    _make_source(client)
    client.post("/controls", data={
        "id": "HDRTITLE1", "title": "Header title", "objective": "o", "narrative": "n",
        "source_ids": ["users"]}, follow_redirects=False)

    page = client.get("/controls/HDRTITLE1")
    assert page.status_code == 200
    assert 'class="control-title-edit"' in page.text
    assert 'action="/controls/HDRTITLE1/title"' in page.text
    assert 'class="control-title-display"' in page.text
    assert 'class="control-title-pencil"' in page.text
    assert 'id="f-title"' not in page.text


def test_edit_control_header_title_editor_updates_title(client):
    _make_source(client)
    client.post("/controls", data={
        "id": "TITLEEDIT1", "title": "Old title", "objective": "o", "narrative": "n",
        "source_ids": ["users"]}, follow_redirects=False)

    resp = client.post(
        "/controls/TITLEEDIT1/title",
        data={"title": "New title from header"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert resp.headers["location"] == "/controls/TITLEEDIT1"
    updated = _get_control(client, "TITLEEDIT1")
    assert updated["title"] == "New title from header"


def test_edit_control_header_splits_title_and_run(client):
    # 2026-06-27 review: the Run button sits to the right of the title (a split
    # header), not crowding it.
    _make_source(client)
    client.post("/controls", data={
        "id": "HDRLAYOUT1", "title": "Layout", "objective": "o", "narrative": "n",
        "source_ids": ["users"]}, follow_redirects=False)
    page = client.get("/controls/HDRLAYOUT1").text
    assert "control-head" in page                       # flex split header
    assert 'action="/controls/HDRLAYOUT1/run"' in page   # Run lives in that header


def test_source_picker_shows_title_and_view_link(client):
    _make_source(client, sid="invoices")
    # Give the source a friendly title.
    client.post("/sources/invoices", data={
        "title": "Vendor Invoice Register",
        "display_name__user_id": "User ID", "data_type__user_id": "text",
        "display_name__can_create": "Can Create", "data_type__can_create": "text",
        "display_name__can_approve": "Can Approve", "data_type__can_approve": "text",
    }, follow_redirects=False)
    page = client.get("/controls/new").text
    assert "Vendor Invoice Register" in page  # friendly title is the label
    assert "(invoices)" in page  # code id shown in parentheses
    # A View link jumps to the source in a new tab.
    assert 'href="/sources/invoices"' in page and 'target="_blank"' in page


# ---------------------------------------------------------------------------
# Task 6: Definition tab is metadata-only
# ---------------------------------------------------------------------------

def test_definition_has_no_test_logic(client):
    """The Definition page must not contain the Test logic section."""
    _make_source(client)
    client.post("/controls", data={
        "id": "tl1", "title": "Test Logic Check", "objective": "o", "narrative": "n",
        "source_ids": ["users"],
    }, follow_redirects=False)
    r = client.get("/controls/tl1")
    assert "Test logic" not in r.text
    assert 'name="test_code"' not in r.text
    assert 'name="test_kind"' not in r.text


def test_editing_metadata_preserves_existing_logic(client):
    """POSTing the Definition form must not clobber the control's logic."""
    cid = _make_rule_control(client)
    before = _get_control(client, cid)
    assert before["rule_spec"] is not None, "setup: control must have a rule_spec"

    # Post a metadata-only update (title changed; no logic fields).
    client.post(f"/controls/{cid}", data={
        "id": cid, "title": "New title",
        "objective": "o", "narrative": "n", "framework_nist": "",
        "failure_threshold_count": "0",
        "source_ids": before["source_ids"],
    }, follow_redirects=False)

    after = _get_control(client, cid)
    assert after["title"] == "New title"
    assert after["rule_spec"] == before["rule_spec"]  # logic untouched


def test_new_control_has_empty_logic(client):
    """A brand-new control created via the Definition form must have no logic."""
    _make_source(client)
    client.post("/controls", data={
        "id": "empty1", "title": "Empty Logic", "objective": "o", "narrative": "n",
        "source_ids": ["users"],
    }, follow_redirects=False)
    c = _get_control(client, "empty1")
    assert c["test_kind"] == "pipeline"
    assert c["rule_spec"] is None
    assert c["test_code"] is None
    assert c["pipeline"] is None


# ---------------------------------------------------------------------------
# F2: source-desync guard — Definition save must not drop logic-required sources
# ---------------------------------------------------------------------------

def _make_cross_source_control(client) -> str:
    """Create a control whose pipeline requires two sources (A and B).

    Uses an Import+Test graph with a not_exists_in cross-source condition so
    source B is required by the Test node condition (not the Import node).
    Returns the control id.
    """
    import json
    # Source A: primary population.
    _make_source(client, "f2_accounts")
    # Source B: the reference set (needed by the cross-source condition).
    csv_b = b"employee_id,status\nE1,active\n"
    client.post("/sources", data={"source_id": "f2_employees", "format": "csv"},
                files={"file": ("f2_employees.csv", __import__("io").BytesIO(csv_b), "text/csv")},
                follow_redirects=False)

    cid = "F2C1"
    client.post("/controls", data={
        "id": cid, "title": "F2 Cross-Source", "objective": "o", "narrative": "n",
        "source_ids": ["f2_accounts", "f2_employees"],
    }, follow_redirects=False)

    # Save a pipeline that references f2_employees via a not_exists_in condition.
    graph = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "f2_accounts", "narrative": ""},
            {"id": "tst", "type": "test", "inputs": ["imp"], "narrative": "",
             "config": {
                 "logic": "all", "severity": "high",
                 "item_key_column": "user_id",
                 "description_template": "User {user_id} missing from employees",
                 "conditions": [{
                     "column": "user_id",
                     "op": "not_exists_in",
                     "other_source": "f2_employees",
                     "this_key": "user_id",
                     "other_key": "employee_id",
                 }],
             }},
        ]
    }
    client.post(f"/controls/{cid}/logic/builder",
                data={"pipeline_json": json.dumps(graph)},
                follow_redirects=False)
    return cid


def test_definition_save_preserves_logic_required_sources(client):
    """Posting the Definition form with source B unchecked must NOT drop B if
    the control's logic (pipeline) still needs it.

    Scenario: the pipeline has a not_exists_in condition that references
    f2_employees (source B).  The author accidentally unchecks B on the Definition
    tab and saves.  The guard must UNION B back in so the next run doesn't fail
    with 'unknown source'.
    """
    cid = _make_cross_source_control(client)

    before = _get_control(client, cid)
    assert "f2_employees" in before["source_ids"], (
        "setup: f2_employees must be bound before the test"
    )

    # POST the Definition form with ONLY source A checked (B unchecked).
    client.post(f"/controls/{cid}", data={
        "id": cid, "title": "F2 Cross-Source",
        "objective": "o", "narrative": "n", "framework_nist": "",
        "source_ids": ["f2_accounts"],  # B intentionally omitted
    }, follow_redirects=False)

    after = _get_control(client, cid)
    assert "f2_employees" in after["source_ids"], (
        "source f2_employees was dropped even though the pipeline still needs it"
    )


def test_definition_add_source_auto_adds_import_node(client):
    import json

    _make_source(client, "def_a")
    _make_source(client, "def_b")
    cid = "DEFSYNC1"
    client.post("/controls", data={
        "id": cid, "title": "Definition sync", "objective": "o", "narrative": "n",
        "source_ids": ["def_a"],
    }, follow_redirects=False)
    graph = {
        "nodes": [
            {"id": "imp_a", "type": "import", "source_id": "def_a", "narrative": ""},
            {"id": "tst", "type": "test", "inputs": ["imp_a"], "narrative": "",
             "config": {"logic": "all", "severity": "medium", "conditions": []}},
        ]
    }
    client.post(f"/controls/{cid}/logic/builder",
                data={"pipeline_json": json.dumps(graph)},
                follow_redirects=False)

    client.post(f"/controls/{cid}", data={
        "id": cid, "title": "Definition sync", "objective": "o", "narrative": "n",
        "framework_nist": "",
        "source_ids": ["def_a", "def_b"],
    }, follow_redirects=False)

    updated = _get_control(client, cid)
    nodes = (updated.get("pipeline") or {}).get("nodes", [])
    import_sources = [n.get("source_id") for n in nodes if n.get("type") == "import"]
    assert "def_b" in import_sources
    assert [n.get("type") for n in nodes[:2]] == ["import", "import"]


def test_definition_remove_source_unimports_and_cleans_inputs(client):
    import json

    _make_source(client, "drop_a")
    _make_source(client, "drop_b")
    cid = "DEFSYNC2"
    client.post("/controls", data={
        "id": cid, "title": "Definition sync", "objective": "o", "narrative": "n",
        "source_ids": ["drop_a", "drop_b"],
    }, follow_redirects=False)
    graph = {
        "nodes": [
            {"id": "imp_a", "type": "import", "source_id": "drop_a", "narrative": ""},
            {"id": "imp_b", "type": "import", "source_id": "drop_b", "narrative": ""},
            {"id": "tst", "type": "test", "inputs": ["imp_a", "imp_b"], "narrative": "",
             "config": {"logic": "all", "severity": "medium", "conditions": []}},
        ]
    }
    client.post(f"/controls/{cid}/logic/builder",
                data={"pipeline_json": json.dumps(graph)},
                follow_redirects=False)

    client.post(f"/controls/{cid}", data={
        "id": cid, "title": "Definition sync", "objective": "o", "narrative": "n",
        "framework_nist": "",
        "source_ids": ["drop_a"],
    }, follow_redirects=False)

    updated = _get_control(client, cid)
    nodes = (updated.get("pipeline") or {}).get("nodes", [])
    import_sources = [n.get("source_id") for n in nodes if n.get("type") == "import"]
    assert "drop_b" not in import_sources
    test_node = next(n for n in nodes if n.get("id") == "tst")
    assert "imp_b" not in list(test_node.get("inputs") or [])


def test_definition_existing_control_enables_source_autosave(client):
    _make_source(client, "auto_src")
    client.post("/controls", data={
        "id": "AUTOSAVE1", "title": "Autosave check", "objective": "o", "narrative": "n",
        "source_ids": ["auto_src"],
    }, follow_redirects=False)

    page = client.get("/controls/AUTOSAVE1")
    assert page.status_code == 200
    assert "data-source-autosave-form" in page.text


def test_threshold_rationale_persists_and_renders(client):
    _make_source(client, "tr_src")
    client.post("/controls", data={
        "id": "TR1", "title": "Threshold rationale", "objective": "o", "narrative": "n",
        "source_ids": ["tr_src"],
        "failure_threshold_count": "2",
        "failure_threshold_rationale": "Up to 2 exceptions is immaterial for this account.",
    }, follow_redirects=False)

    stored = _get_control(client, "TR1")
    assert stored["failure_threshold_rationale"] == \
        "Up to 2 exceptions is immaterial for this account."

    page = client.get("/controls/TR1")
    assert "Threshold rationale" in page.text            # the field label
    assert "immaterial for this account" in page.text    # the saved value renders


def test_threshold_rationale_survives_title_edit(client):
    """Regression (learning 0023): a title-only update must not NULL the rationale."""
    _make_source(client, "tr_src2")
    client.post("/controls", data={
        "id": "TR2", "title": "Before", "objective": "o", "narrative": "n",
        "source_ids": ["tr_src2"],
        "failure_threshold_rationale": "Documented tolerance.",
    }, follow_redirects=False)
    assert _get_control(client, "TR2")["failure_threshold_rationale"] == "Documented tolerance."

    # The header pencil posts only the title through a separate handler.
    client.post("/controls/TR2/title", data={"title": "After"}, follow_redirects=False)

    updated = _get_control(client, "TR2")
    assert updated["title"] == "After"
    assert updated["failure_threshold_rationale"] == "Documented tolerance."  # not nulled
