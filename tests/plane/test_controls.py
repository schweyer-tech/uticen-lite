import io

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect


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
