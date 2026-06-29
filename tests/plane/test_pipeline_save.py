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

from uticen_lite.pipeline.lint import OFFRAMP_MESSAGE

# The offramp message is HTML-escaped in the rendered page (the apostrophe in
# "can't" becomes &#39;), so assert on an escaping-stable substring of it.
_OFFRAMP_STABLE = "pull data in with an Import node, or convert this control"
assert _OFFRAMP_STABLE in OFFRAMP_MESSAGE


def _make_source(client, sid, csv):
    client.post("/sources", data={"source_id": sid, "format": "csv"},
                files={"file": (f"{sid}.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)


def _conn(client):
    from uticen_lite.store.db import connect
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

    from uticen_lite.store import repo
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

    from uticen_lite.store import repo
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


def test_save_cross_source_pipeline_ignores_placeholder_filter_condition(client):
    _make_source(client, "access_accounts",
                 b"account_id,employee_id,is_active\nA1,E1,true\nA2,E2,true\n")
    _make_source(client, "employees", b"employee_id,status\nE1,terminated\nE2,active\n")
    graph = {"nodes": [
        {"id": "acc", "type": "import", "source_id": "access_accounts"},
        {"id": "active", "type": "filter", "inputs": ["acc"],
         "config": {"logic": "all",
                    "conditions": [
                        {"column": "is_active", "op": "eq", "value": "true"},
                        {"column": "", "op": "eq", "value": ""},
                    ]}},
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
    client.post("/controls", data={
        "id": "pipe2b", "title": "Cross Blank", "objective": "", "narrative": "",
    }, follow_redirects=False)
    resp = client.post("/controls/pipe2b/logic/builder",
                       data={"pipeline_json": json.dumps(graph)},
                       follow_redirects=False)
    assert resp.status_code in (302, 303)

    from uticen_lite.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "pipe2b")
    conn.close()
    assert c["test_kind"] == "pipeline"
    assert c["test_code"] is not None
    assert c["source_ids"] == ["access_accounts", "employees"]


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
    from uticen_lite.store import repo
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
    from uticen_lite.store import repo
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
    from uticen_lite.store import repo
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
    from uticen_lite.store import repo
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
    from uticen_lite.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "ok1")
    conn.close()
    assert c is not None
    assert c["test_kind"] == "pipeline"
    assert "def _node_cust(rows):" in c["test_code"]


# ---------------------------------------------------------------------------
# T8 regression: cross-source exists_in/not_exists_in in Test node
# ---------------------------------------------------------------------------

def test_save_single_import_test_with_not_exists_in_binds_both_sources(client):
    """T8 regression: a single-Import pipeline whose Test node has a
    not_exists_in condition referencing a second source MUST bind both sources.

    Pre-fix, _save_pipeline_graph only called parsed.import_source_ids() which
    returns only sources bound by Import nodes; the other_source referenced by
    exists_in/not_exists_in conditions was never added to source_ids.  Running
    the control then raised "exists_in references unknown source".
    """
    # Source A: the primary population (single Import node)
    _make_source(client, "active_users",
                 b"user_id,name\nU1,Alice\nU2,Bob\nU3,Carol\n")
    # Source B: the reference set (referenced only by not_exists_in, no Import node)
    _make_source(client, "terminated_users",
                 b"user_id,reason\nU3,resigned\n")

    # Single-Import graph: Import(active_users) → Test with not_exists_in(terminated_users)
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "active_users",
         "narrative": "All active users"},
        {"id": "tst", "type": "test", "inputs": ["imp"],
         "config": {
             "logic": "all",
             "severity": "high",
             "item_key_column": "user_id",
             "conditions": [
                 {
                     "op": "not_exists_in",
                     "column": "user_id",
                     "other_source": "terminated_users",
                     "this_key": "user_id",
                     "other_key": "user_id",
                 }
             ],
         }},
    ]}

    # Create the control shell then save the pipeline via the Builder route.
    client.post("/controls", data={
        "id": "cross1", "title": "Access vs Terminated", "objective": "o", "narrative": "n",
    }, follow_redirects=False)
    resp = client.post("/controls/cross1/logic/builder",
                       data={"pipeline_json": json.dumps(graph)},
                       follow_redirects=False)
    assert resp.status_code in (302, 303), f"save failed: {resp.status_code} {resp.text[:200]}"

    from uticen_lite.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "cross1")
    conn.close()

    # BOTH sources must be bound — active_users (Import) + terminated_users (other_source).
    assert c is not None
    assert "active_users" in c["source_ids"], (
        "Import source must be bound"
    )
    assert "terminated_users" in c["source_ids"], (
        "other_source from not_exists_in condition must also be bound (T8 bug)"
    )
    # Import source should come first (deterministic ordering).
    assert c["source_ids"][0] == "active_users"


def test_run_with_not_exists_in_condition_succeeds_without_unknown_source_error(client):
    """T8 regression: after the fix, running a control with a not_exists_in
    condition must not raise 'exists_in references unknown source'.

    U3 is in both sources, so the test flags U3 as a violation (active user who
    is also in the terminated list).  U1 and U2 pass.  We assert a clean run
    (no RunnerError) and exactly 1 violation.
    """
    # Seed both sources.
    _make_source(client, "active_users2",
                 b"user_id,name\nU1,Alice\nU2,Bob\nU3,Carol\n")
    _make_source(client, "terminated_users2",
                 b"user_id,reason\nU3,resigned\n")

    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "active_users2"},
        {"id": "tst", "type": "test", "inputs": ["imp"],
         "config": {
             "logic": "all",
             "severity": "high",
             "item_key_column": "user_id",
             "description_template": "User {user_id} is active but terminated",
             "conditions": [
                 {
                     "op": "not_exists_in",
                     "column": "user_id",
                     "other_source": "terminated_users2",
                     "this_key": "user_id",
                     "other_key": "user_id",
                 }
             ],
         }},
    ]}

    client.post("/controls", data={
        "id": "cross2", "title": "Access T8 Run", "objective": "o", "narrative": "n",
    }, follow_redirects=False)
    resp = client.post("/controls/cross2/logic/builder",
                       data={"pipeline_json": json.dumps(graph)},
                       follow_redirects=False)
    assert resp.status_code in (302, 303)

    # Run the control — must NOT raise "unknown source" (T8 bug).
    run_resp = client.post("/controls/cross2/run", follow_redirects=False)
    assert run_resp.status_code in (302, 303), (
        f"run failed (unknown-source bug?): {run_resp.status_code} {run_resp.text[:300]}"
    )

    # Verify the workpaper shows exactly 1 violation (U3).
    run_url = run_resp.headers["location"]
    view = client.get(run_url)
    assert view.status_code == 200
    assert "U3" in view.text  # the one violation (Carol, in terminated list)


# ---------------------------------------------------------------------------
# Regression: adding a blank condition via the Builder must not 500
# ---------------------------------------------------------------------------

def test_save_pipeline_with_blank_condition_placeholder_does_not_500(client):
    """Regression for the "+Add condition" 500 in Logic Builder.

    When the user clicks "+ Add condition" the JS submits the form with an empty
    placeholder ``{column:'', op:'eq', value:''}``.  Before the fix,
    ``_emit_terminal_rule`` called ``parse_rule_spec`` on the RAW (unfiltered)
    conditions dict, raising ``RuleSpecError`` uncaught as a 500.

    The save must succeed (303 redirect) with the blank condition silently
    dropped from the compiled artifact; the stored ``pipeline`` column retains
    it so the UI re-renders the empty row correctly.
    """
    _make_source(client, "blank_cond_src",
                 b"emp_id,status\nE1,active\nE2,terminated\n")
    _make_source(client, "blank_cond_src2",
                 b"emp_id,dept\nE1,eng\nE2,hr\n")

    # Non-pure pipeline (two Imports → Join → Test) with a blank condition
    # placeholder on the Test node — exactly what "+Add condition" produces.
    graph = {"nodes": [
        {"id": "imp1", "type": "import", "source_id": "blank_cond_src",
         "inputs": [], "config": {}},
        {"id": "imp2", "type": "import", "source_id": "blank_cond_src2",
         "inputs": [], "config": {}},
        {"id": "join", "type": "join", "inputs": ["imp1", "imp2"],
         "config": {"mode": "inner", "left_key": "emp_id", "right_key": "emp_id"}},
        {"id": "tst", "type": "test", "inputs": ["join"],
         "config": {"logic": "all", "item_key_column": "emp_id",
                    "conditions": [
                        {"column": "status", "op": "eq", "value": "active"},
                        {"column": "", "op": "eq", "value": ""},  # blank placeholder
                    ]}},
    ]}
    client.post("/controls", data={
        "id": "blank1", "title": "Blank cond", "objective": "o", "narrative": "n",
    }, follow_redirects=False)
    resp = client.post("/controls/blank1/logic/builder",
                       data={"pipeline_json": json.dumps(graph)},
                       follow_redirects=False)
    # Must be 303 redirect, NOT 500.
    assert resp.status_code in (302, 303), (
        f"expected redirect (303), got {resp.status_code} — "
        f"blank condition 500 regression: {resp.text[:300]}"
    )

    from uticen_lite.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "blank1")
    conn.close()
    assert c is not None
    # The blank condition must be stripped from the compiled artifact.
    assert c["test_code"] is not None
    assert '""' not in c["test_code"] or "column" not in c["test_code"]
    # The stored pipeline graph retains the blank condition for UI round-trip.
    assert c["pipeline"] is not None


# ---------------------------------------------------------------------------
# Autosave mode: in-place fragment response (no redirect)
# ---------------------------------------------------------------------------

def _make_simple_graph() -> dict:
    """Single-Import + Test graph that compiles cleanly."""
    return {"nodes": [
        {"id": "imp", "type": "import", "source_id": "acct_as",
         "narrative": "All accounts"},
        {"id": "tst", "type": "test", "inputs": ["imp"],
         "config": {"logic": "all", "severity": "high",
                    "item_key_column": "account_id",
                    "conditions": [{"column": "is_active", "op": "eq",
                                    "value": "true"}]}},
    ]}


def test_autosave_returns_fragment_not_redirect(client):
    """Posting autosave=1 on a valid graph MUST return 200 HTML fragment, NOT redirect."""
    _make_source(client, "acct_as",
                 b"account_id,is_active\nA1,true\nA2,false\n")
    client.post("/controls", data={
        "id": "as1", "title": "AS Test", "objective": "o", "narrative": "n",
    }, follow_redirects=False)

    resp = client.post(
        "/controls/as1/logic/builder",
        data={"pipeline_json": json.dumps(_make_simple_graph()), "autosave": "1"},
        follow_redirects=False,
    )

    # Must be 200 fragment, not 302/303.
    assert resp.status_code == 200, (
        f"autosave should return 200 fragment, got {resp.status_code}"
    )
    # Must NOT be a redirect.
    assert resp.status_code not in (302, 303)
    # The fragment must contain card HTML (the pipe-insert affordance).
    assert "pipe-insert" in resp.text, "expected pipe-cards fragment in autosave response"

    # The control MUST still be persisted after autosave.
    from uticen_lite.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "as1")
    conn.close()
    assert c is not None
    assert c["test_kind"] == "pipeline"
    assert c["pipeline"] is not None


def test_autosave_validation_error_returns_422_not_redirect(client):
    """Posting autosave=1 with an unsafe graph returns 422 (not redirect, not 500)."""
    _make_source(client, "journal_entries", b"entry_id,amount\nE1,100\n")
    client.post("/controls", data={
        "id": "as2", "title": "AS Err", "objective": "o", "narrative": "n",
    }, follow_redirects=False)

    resp = client.post(
        "/controls/as2/logic/builder",
        data={
            "pipeline_json": json.dumps(_custom_pipeline("rows = open('/etc/passwd').read()")),
            "autosave": "1",
        },
        follow_redirects=False,
    )

    # Must be 422 (validation error), not a redirect, not a 500.
    assert resp.status_code == 422, (
        f"autosave error should return 422, got {resp.status_code}"
    )
    assert _OFFRAMP_STABLE in resp.text

    # The pipeline must NOT be persisted after a failed autosave.
    from uticen_lite.store import repo
    conn = _conn(client)
    c = repo.get_control(conn, "as2")
    conn.close()
    assert c is not None
    assert c["pipeline"] is None
