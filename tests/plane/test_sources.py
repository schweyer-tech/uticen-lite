import io


def test_upload_creates_source_with_inferred_columns(client):
    csv = b"user_id,can_create,can_approve\nU1,true,false\n"
    resp = client.post(
        "/sources",
        data={"source_id": "users", "format": "csv"},
        files={"file": ("users.csv", io.BytesIO(csv), "text/csv")},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    edit = client.get("/sources/users")
    assert edit.status_code == 200
    for col in ("user_id", "can_create", "can_approve"):
        assert col in edit.text


def test_save_column_mapping(client):
    csv = b"user_id,amount\nU1,5\n"
    client.post("/sources", data={"source_id": "tx", "format": "csv"},
                files={"file": ("tx.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)
    resp = client.post("/sources/tx", data={
        "key_columns": "user_id",
        "display_name__user_id": "User ID", "data_type__user_id": "text",
        "is_key__user_id": "on", "include__user_id": "on",
        "display_name__amount": "Amount", "data_type__amount": "number",
        "include__amount": "on",
    }, follow_redirects=False)
    assert resp.status_code in (302, 303)
    # persisted
    from controlflow_sdk.store import repo
    from controlflow_sdk.store.db import connect
    src = repo.get_source(connect(client.app.state.project_root), "tx")
    assert src["key_config"] == {"mode": "single", "columns": ["user_id"]}
    amount = next(c for c in src["columns"] if c["original_name"] == "amount")
    assert amount["data_type"] == "number" and amount["display_name"] == "Amount"
