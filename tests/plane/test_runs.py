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


def _run_id_of(client):
    resp = client.post("/controls/sod/run", follow_redirects=False)
    return resp.headers["location"].rsplit("/", 1)[-1]


def test_history_lists_multiple_runs(client):
    _rule_control(client)
    first_id = _run_id_of(client)
    second_id = _run_id_of(client)
    assert first_id != second_id            # distinct executed_at → distinct ids

    page = client.get("/controls/sod/history")
    assert page.status_code == 200
    # both runs appear, each linking to its own run view
    assert f'/controls/sod/runs/{first_id}' in page.text
    assert f'/controls/sod/runs/{second_id}' in page.text
    # result badge present
    assert "% pass" in page.text
    # newest-first: the SECOND (latest) run id appears before the first in the HTML
    assert page.text.index(second_id) < page.text.index(first_id)


def test_history_empty_state(client):
    _rule_control(client)                    # control exists, never run
    page = client.get("/controls/sod/history")
    assert page.status_code == 200
    assert "Not yet run" in page.text
    assert 'action="/controls/sod/run"' in page.text


def test_history_trend_renders_svg(client):
    _rule_control(client)
    _run_id_of(client)
    _run_id_of(client)
    page = client.get("/controls/sod/history")
    assert "<svg" in page.text
    assert "<polyline" in page.text
    # tokenized color, not a hard-coded hex (learning 0005)
    assert "var(--accent-primary)" in page.text


def test_control_page_has_history_tab(client):
    _rule_control(client)
    edit = client.get("/controls/sod")
    assert 'href="/controls/sod/history"' in edit.text
    assert 'class="tabs"' in edit.text
    # a brand-new control has no id → no tabs nav
    new = client.get("/controls/new")
    assert 'class="tabs"' not in new.text


def test_dashboard_links_to_history(client):
    _rule_control(client)
    _run_id_of(client)
    home = client.get("/")
    assert 'href="/controls/sod/history"' in home.text
