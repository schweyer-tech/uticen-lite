import io


def _rule_control(client):
    csv = b"user_id,can_create,can_approve\nU1,true,true\nU2,true,false\n"
    client.post("/sources", data={"source_id": "users", "format": "csv"},
                files={"file": ("users.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)
    client.post("/controls", data={
        "id": "sod", "title": "SoD", "objective": "o", "narrative": "n",
        "test_kind": "rule", "rule_logic": "all", "rule_severity": "high",
        "rule_description": "User {user_id}", "rule_item_key": "user_id",
        "cond_column": ["can_create", "can_approve"], "cond_op": ["eq", "eq"],
        "cond_value": ["true", "true"], "source_ids": ["users"],
        "failure_threshold_count": "0",
    }, follow_redirects=False)


def test_run_then_view(client):
    _rule_control(client)
    resp = client.post("/controls/sod/run", follow_redirects=False)
    assert resp.status_code in (302, 303)
    run_url = resp.headers["location"]
    view = client.get(run_url)
    assert view.status_code == 200
    assert "U1" in view.text                 # the one violation
    assert "1" in view.text                  # failed count present
