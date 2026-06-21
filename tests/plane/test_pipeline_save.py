"""Minimal control-plane wiring for saving a pipeline control (issue #25).

Stage 1 proves the save path: a posted graph is validated, its source binding
is DERIVED from the Import nodes, it compiles to a rule_spec/test_code artifact,
and the store row carries test_kind='pipeline'.

Stage 2 (§8 layer 1) adds the save-time allowlist deny-scan: a Custom Python
node that could read a file / reach outside ``rows`` is REFUSED at save (the
control is not persisted) and the offramp message reaches the author.
"""

from __future__ import annotations

import io
import json

from controlflow_sdk.pipeline.lint import OFFRAMP_MESSAGE

# The offramp message is HTML-escaped in the rendered page (the apostrophe in
# "can't" becomes &#39;), so assert on an escaping-stable substring of it.
_OFFRAMP_STABLE = "pull data in with an Import node, or convert this control"
assert _OFFRAMP_STABLE in OFFRAMP_MESSAGE


def _make_source(client, sid, csv):
    client.post("/sources", data={"source_id": sid, "format": "csv"},
                files={"file": (f"{sid}.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)


def _conn(client):
    from controlflow_sdk.store.db import connect
    return connect(client.app.state.project_root)


def test_save_pure_pipeline_derives_sources_and_compiles_to_rule_spec(client):
    _make_source(client, "accounts",
                 b"account_id,is_active,is_privileged\nA1,true,true\nA2,false,true\n")
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "accounts",
         "narrative": "All accounts"},
        {"id": "tst", "type": "test", "inputs": ["imp"],
         "config": {"logic": "all", "severity": "high", "item_key_column": "account_id",
                    "conditions": [{"column": "is_privileged", "op": "eq", "value": True}]}},
    ]}
    # Step 1: create metadata shell.
    client.post("/controls", data={
        "id": "pipe1", "title": "Pipe", "objective": "o", "narrative": "n",
    }, follow_redirects=False)
    # Step 2: author logic via Builder.
    resp = client.post("/controls/pipe1/logic/builder",
                       data={"pipeline_json": json.dumps(graph)},
                       follow_redirects=False)
    assert resp.status_code in (302, 303)

    from controlflow_sdk.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "pipe1")
    conn.close()
    assert c["test_kind"] == "pipeline"
    assert c["pipeline"] == graph
    # Source binding derived from the Import node (no separate picker).
    assert c["source_ids"] == ["accounts"]
    # Pure single-source → compiled to a rule_spec artifact.
    assert c["rule_spec"] is not None
    assert any(cond["column"] == "is_privileged" for cond in c["rule_spec"]["conditions"])
    assert c["test_code"] is None


def test_save_cross_source_pipeline_compiles_to_test_code(client):
    _make_source(client, "access_accounts",
                 b"account_id,employee_id,is_active\nA1,E1,true\nA2,E2,true\n")
    _make_source(client, "employees", b"employee_id,status\nE1,terminated\nE2,active\n")
    graph = {"nodes": [
        {"id": "acc", "type": "import", "source_id": "access_accounts"},
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
                    "conditions": [{"column": "account_id", "op": "not_empty"}]}},
    ]}
    # Step 1: create metadata shell.
    client.post("/controls", data={
        "id": "pipe2", "title": "Cross", "objective": "", "narrative": "",
    }, follow_redirects=False)
    # Step 2: author logic via Builder.
    resp = client.post("/controls/pipe2/logic/builder",
                       data={"pipeline_json": json.dumps(graph)},
                       follow_redirects=False)
    assert resp.status_code in (302, 303)

    from controlflow_sdk.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "pipe2")
    conn.close()
    assert c["test_kind"] == "pipeline"
    # Both Import sources bound, in node order (access_accounts primary).
    assert c["source_ids"] == ["access_accounts", "employees"]
    # Cross-source → compiled to a test() string.
    assert c["test_code"] is not None
    assert "def test(pop, sources):" in c["test_code"]
    assert c["rule_spec"] is None


# ---------------------------------------------------------------------------
# §8 layer 1: allowlist deny-scan at SAVE
# ---------------------------------------------------------------------------

def _custom_pipeline(code: str, flavor: str = "transform") -> dict:
    return {"nodes": [
        {"id": "imp", "type": "import", "source_id": "journal_entries"},
        {"id": "cust", "type": "custom_python", "inputs": ["imp"],
         "config": {"flavor": flavor, "code": code}},
        {"id": "tst", "type": "test", "inputs": ["cust"],
         "config": {"logic": "any", "item_key_column": "entry_id",
                    "conditions": [{"column": "entry_id", "op": "not_empty"}]}},
    ]}


def _post_pipeline(client, cid: str, graph: dict):
    """Create a bare control shell then POST the graph to the Builder route.

    Returns the response from the Builder POST (which may be 303 on success or
    422 on lint failure), mirroring what the tests assert against.
    """
    client.post("/controls", data={
        "id": cid, "title": "JE", "objective": "o", "narrative": "n",
    }, follow_redirects=False)
    return client.post(f"/controls/{cid}/logic/builder",
                       data={"pipeline_json": json.dumps(graph)},
                       follow_redirects=False)


def test_save_rejects_custom_node_that_reads_a_file(client):
    _make_source(client, "journal_entries", b"entry_id,amount\nE1,100\n")
    resp = _post_pipeline(client, "bad1",
                          _custom_pipeline("rows = open('/etc/passwd').read()"))
    # Refused: re-rendered edit form (422), NOT a 303 redirect, NOT a 500.
    assert resp.status_code == 422
    assert _OFFRAMP_STABLE in resp.text
    # The control shell exists but the unsafe pipeline was NOT persisted.
    from controlflow_sdk.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "bad1")
    conn.close()
    assert c is not None
    assert c["pipeline"] is None


def test_save_rejects_custom_node_using_read_csv(client):
    _make_source(client, "journal_entries", b"entry_id,amount\nE1,100\n")
    resp = _post_pipeline(client, "bad2",
                          _custom_pipeline("rows = pd.read_csv('/secret.csv')"))
    assert resp.status_code == 422
    assert "read_csv" in resp.text
    assert _OFFRAMP_STABLE in resp.text
    from controlflow_sdk.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "bad2")
    conn.close()
    assert c is not None and c["pipeline"] is None


def test_save_rejects_custom_node_using_dunder_import(client):
    _make_source(client, "journal_entries", b"entry_id,amount\nE1,100\n")
    resp = _post_pipeline(client, "bad3",
                          _custom_pipeline("m = __import__('os')\nrows = rows"))
    assert resp.status_code == 422
    assert "__import__" in resp.text
    from controlflow_sdk.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "bad3")
    conn.close()
    assert c is not None and c["pipeline"] is None


def test_save_rejects_custom_node_using_eval(client):
    _make_source(client, "journal_entries", b"entry_id,amount\nE1,100\n")
    resp = _post_pipeline(client, "bad4",
                          _custom_pipeline("rows = eval('rows')"))
    assert resp.status_code == 422
    assert "eval" in resp.text
    from controlflow_sdk.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "bad4")
    conn.close()
    assert c is not None and c["pipeline"] is None


def test_save_accepts_clean_custom_transform_node(client):
    _make_source(client, "journal_entries", b"entry_id,amount\nE1,100\nE2,50\n")
    graph = _custom_pipeline(
        "rows = rows[rows['amount'].astype(float) >= 100]")
    resp = _post_pipeline(client, "ok1", graph)
    # A clean rows→rows node saves normally (303 redirect to the control).
    assert resp.status_code in (302, 303)
    from controlflow_sdk.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "ok1")
    conn.close()
    assert c is not None
    assert c["test_kind"] == "pipeline"
    assert "def _node_cust(rows):" in c["test_code"]
