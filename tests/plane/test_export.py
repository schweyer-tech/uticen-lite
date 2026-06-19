import io
import json
import zipfile


def test_export_no_runs_returns_400_not_500(client):
    """POST /export with no runs must return 400 with a helpful message, not 500."""
    resp = client.post("/export")
    assert resp.status_code != 500, (
        "POST /export with no runs raised an unhandled 500; "
        "expected a 400 with a 'run a control first' message"
    )
    assert resp.status_code == 400
    body = resp.json()
    # Body must mention the missing-run condition
    combined = json.dumps(body).lower()
    assert "run" in combined, f"Response body does not mention 'run': {body}"


def _ran_control(client):
    csv = b"user_id,can_create,can_approve\nU1,true,true\n"
    client.post(
        "/sources",
        data={"source_id": "users", "format": "csv"},
        files={"file": ("users.csv", io.BytesIO(csv), "text/csv")},
        follow_redirects=False,
    )
    client.post(
        "/controls",
        data={
            "id": "sod",
            "title": "SoD",
            "objective": "o",
            "narrative": "n",
            "test_kind": "rule",
            "rule_logic": "all",
            "rule_severity": "high",
            "rule_description": "User {user_id}",
            "rule_item_key": "user_id",
            "cond_column": ["can_create"],
            "cond_op": ["eq"],
            "cond_value": ["true"],
            "source_ids": ["users"],
            "failure_threshold_count": "0",
        },
        follow_redirects=False,
    )
    client.post("/controls/sod/run", follow_redirects=False)


def test_export_returns_valid_bundle(client):
    _ran_control(client)
    resp = client.post("/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] in (
        "application/zip",
        "application/x-zip-compressed",
    )
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["schema_version"] == "1.0"
    assert any(c["id"] == "sod" for c in manifest["controls"])
