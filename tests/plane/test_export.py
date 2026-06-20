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


def _ran_cross_source_control(client):
    access = b"user_id\nU1\nU2\nU3\n"
    hr = b"employee_id,name\nU1,Ann\nU3,Cara\n"
    client.post("/sources", data={"source_id": "access", "format": "csv"},
                files={"file": ("access.csv", io.BytesIO(access), "text/csv")},
                follow_redirects=False)
    client.post("/sources", data={"source_id": "hr_roster", "format": "csv"},
                files={"file": ("hr_roster.csv", io.BytesIO(hr), "text/csv")},
                follow_redirects=False)
    client.post("/controls", data={
        "id": "term", "title": "Terminated access", "objective": "o", "narrative": "n",
        "test_kind": "rule", "rule_logic": "all", "rule_severity": "high",
        "rule_description": "User {user_id} retains access", "rule_item_key": "user_id",
        "cond_column": ["user_id"], "cond_op": ["not_exists_in"], "cond_value": [""],
        "cond_other_source": ["hr_roster"], "cond_this_key": ["user_id"],
        "cond_other_key": ["employee_id"],
        "source_ids": ["access"],  # B auto-bound from the cross-source condition
        "failure_threshold_count": "0",
    }, follow_redirects=False)
    client.post("/controls/term/run", follow_redirects=False)


def test_export_cross_source_control_bundle_valid(client):
    """A cross-source rule control exports a schema-valid bundle whose
    control.test_code is non-empty runnable Python (cardinal rule 0001)."""
    _ran_cross_source_control(client)
    resp = client.post("/export")
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["schema_version"] == "1.0"
    term = next(c for c in manifest["controls"] if c["id"] == "term")
    assert "def test(pop, sources)" in term["test_code"]
    # source B was auto-bound so the runner can load it
    assert {"access", "hr_roster"} <= {s["id"] for s in term["sources"]}


def _ran_pipeline_control(client):
    """A cross-source VISUAL pipeline control (issue #25): Import(access) →
    Filter → Join(inner against employees filtered to terminated) → Test."""
    import json as _json

    access = b"account_id,employee_id,is_active\nA1,E1,true\nA2,E2,true\nA3,E3,true\n"
    emp = b"employee_id,status\nE1,terminated\nE2,active\nE3,terminated\n"
    client.post("/sources", data={"source_id": "access_accounts", "format": "csv"},
                files={"file": ("access_accounts.csv", io.BytesIO(access), "text/csv")},
                follow_redirects=False)
    client.post("/sources", data={"source_id": "employees", "format": "csv"},
                files={"file": ("employees.csv", io.BytesIO(emp), "text/csv")},
                follow_redirects=False)
    graph = {"nodes": [
        {"id": "acc", "type": "import", "source_id": "access_accounts",
         "narrative": "All access accounts"},
        {"id": "active", "type": "filter", "inputs": ["acc"],
         "config": {"logic": "all",
                    "conditions": [{"column": "is_active", "op": "eq", "value": "true"}]}},
        {"id": "emp", "type": "import", "source_id": "employees"},
        {"id": "term", "type": "filter", "inputs": ["emp"],
         "config": {"logic": "all",
                    "conditions": [{"column": "status", "op": "eq", "value": "terminated"}]}},
        {"id": "join", "type": "join", "inputs": ["active", "term"],
         "config": {"left_key": "employee_id", "right_key": "employee_id", "mode": "inner"}},
        {"id": "tst", "type": "test", "inputs": ["join"],
         "config": {"logic": "any", "severity": "critical", "item_key_column": "account_id",
                    "description_template": "Account {account_id} belongs to a terminated employee",
                    "conditions": [{"column": "account_id", "op": "not_empty"}]}},
    ]}
    client.post("/controls", data={
        "id": "term_pipe", "title": "Terminated access (visual)",
        "objective": "o", "narrative": "n",
        "test_kind": "pipeline", "pipeline_json": _json.dumps(graph),
        "failure_threshold_count": "0",
    }, follow_redirects=False)
    client.post("/controls/term_pipe/run", follow_redirects=False)


def test_export_pipeline_control_bundle_valid_and_node_free(client):
    """A VISUAL pipeline control exports a schema-valid bundle whose test_code is
    the compiled cross-source Python — and the bundle never carries the graph or
    the word 'node' (cardinal rule 0001; store-only graph per learning 0006)."""
    from controlflow_sdk.schema.validate import validate_bundle

    _ran_pipeline_control(client)
    resp = client.post("/export")
    assert resp.status_code == 200
    raw = resp.content
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        manifest_bytes = zf.read("manifest.json")
        manifest = json.loads(manifest_bytes)

    assert validate_bundle(manifest) == []
    assert manifest["schema_version"] == "1.0"
    pipe = next(c for c in manifest["controls"] if c["id"] == "term_pipe")
    assert "def test(pop, sources)" in pipe["test_code"]
    assert {"access_accounts", "employees"} <= {s["id"] for s in pipe["sources"]}
    # The flagged account is the active account of a terminated employee.
    run = pipe["runs"][0]
    assert run["failed"] == 2  # A1 (E1) and A3 (E3) are terminated
    # The store-only graph never enters the bundle: no "pipeline" graph, no "node".
    assert "pipeline" not in pipe
    assert b'"node' not in manifest_bytes
    assert '"nodes"' not in manifest_bytes.decode("utf-8")
