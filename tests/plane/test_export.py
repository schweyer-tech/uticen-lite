import io
import json
import zipfile


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
