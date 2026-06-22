import io
import json


def _rule_control(client):
    csv = b"user_id,can_create,can_approve\nU1,true,true\nU2,true,false\n"
    client.post("/sources", data={"source_id": "users", "format": "csv"},
                files={"file": ("users.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)
    client.post("/controls", data={
        "id": "sod", "title": "SoD", "objective": "o", "narrative": "n",
        "source_ids": ["users"],
        "failure_threshold_count": "0",
    }, follow_redirects=False)
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "users"},
        {"id": "tst", "type": "test", "inputs": ["imp"],
         "config": {"logic": "all", "severity": "high", "item_key_column": "user_id",
                    "description_template": "User {user_id}",
                    "conditions": [
                        {"column": "can_create", "op": "eq", "value": True},
                        {"column": "can_approve", "op": "eq", "value": True},
                    ]}},
    ]}
    client.post("/controls/sod/logic/builder",
                data={"pipeline_json": json.dumps(graph)},
                follow_redirects=False)


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
    # legibility scaffolding (U3): a 0/50/100% Y scale, gridlines, and a legend so a
    # reviewer can read pass-rate over runs without guessing.
    assert "trend-legend" in page.text
    assert "Pass rate" in page.text
    assert "Exceptions" in page.text
    assert "trend-grid" in page.text
    assert ">100%<" in page.text and ">50%<" in page.text and ">0%<" in page.text


def test_trend_svg_has_no_invalid_height_attr(client):
    """B1: an SVG `height="auto"` is invalid and errors in the browser console.

    The svg must be sized via CSS, never carry a literal `height="auto"` attribute.
    """
    _rule_control(client)
    _run_id_of(client)
    _run_id_of(client)
    page = client.get("/controls/sod/history")
    assert 'height="auto"' not in page.text
    # the svg element itself declares no width/height presentation attribute
    svg_open = page.text[page.text.index("<svg"):page.text.index("<svg") + 400]
    assert "height=" not in svg_open
    assert "width=" not in svg_open


def test_trend_colors_route_through_tokens(client):
    """Learning 0005: every trend color is a var(--token) in the stylesheet."""
    css = client.get("/static/app.css").text
    # the trend block exists and drives its colors through tokens, no raw hex
    block = css[css.index(".trend-figure"):css.index(".trend-figure") + 1400]
    assert "var(--accent-primary)" in block
    assert "var(--status-warning)" in block
    assert "var(--status-critical)" in block
    assert "#" not in block  # no hard-coded hex colors in the trend rules


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
