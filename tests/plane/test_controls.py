import io


def _make_source(client, sid="users"):
    csv = b"user_id,can_create,can_approve\nU1,true,false\n"
    client.post("/sources", data={"source_id": sid, "format": "csv"},
                files={"file": (f"{sid}.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)


def test_create_python_control(client):
    _make_source(client)
    resp = client.post("/controls", data={
        "id": "py1", "title": "Py", "objective": "o", "narrative": "n",
        "framework_nist": "AC-2, AC-5", "test_kind": "python",
        "test_code": "def test(pop):\n    return []",
        "source_ids": ["users"],
    }, follow_redirects=False)
    assert resp.status_code in (302, 303)
    from controlflow_sdk.store import repo
    from controlflow_sdk.store.db import connect
    conn = connect(client.app.state.project_root)
    c = repo.get_control(conn, "py1")
    conn.close()
    assert c["test_kind"] == "python"
    assert c["framework_refs"] == {"nist": ["AC-2", "AC-5"]}
    assert c["source_ids"] == ["users"]


def test_edit_control_shows_values(client):
    _make_source(client)
    client.post("/controls", data={
        "id": "py2", "title": "Editable", "objective": "o", "narrative": "n",
        "test_kind": "python", "test_code": "def test(pop):\n    return []",
        "source_ids": ["users"]}, follow_redirects=False)
    page = client.get("/controls/py2")
    assert page.status_code == 200
    assert "Editable" in page.text
