from pathlib import Path

from fastapi.testclient import TestClient

from uticen_lite.store import repo
from uticen_lite.store.db import connect


def test_settings_page_has_reset_form(client: TestClient):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert 'action="/settings/reset-demo"' in resp.text


def test_reset_button_is_destructive_not_primary(client: TestClient):
    """E5/F1: the irreversible reset must read as destructive, and the stylesheet
    must define a danger variant rather than blanket-promoting every submit to
    primary-blue (F2)."""
    page = client.get("/settings").text
    assert 'class="btn btn-danger"' in page  # reset carries the danger variant
    css = client.get("/static/app.css").text
    assert ".btn-danger" in css
    # the primary rule no longer hijacks every submit button (selector-list form gone)
    assert ', button[type="submit"]' not in css


def test_reset_demo_wipes_junk_and_reloads(client: TestClient, engagement: Path):
    # Seed a junk control into the engagement store the way a corrupted
    # engagement would carry one.
    conn = connect(engagement)
    repo.upsert_control(
        conn,
        id="JUNK.1",
        title="junk",
        objective="",
        narrative="",
        framework_refs={},
        test_kind="rule",
        rule_spec={},
    )
    conn.close()

    resp = client.post("/settings/reset-demo", follow_redirects=False)
    assert resp.status_code in (302, 303)

    # The demo controls now render on the dashboard and the junk one is gone.
    page = client.get("/").text
    assert "Finance.AP.1" in page
    conn = connect(engagement)
    try:
        assert repo.get_control(conn, "JUNK.1") is None
    finally:
        conn.close()


def test_reset_demo_yields_runnable_engagement(client: TestClient):
    client.post("/settings/reset-demo", follow_redirects=False)
    # After a reset the demo data is in place, so running a demo control must not
    # 500 (the reset is the one-click recovery from a corrupted, un-runnable state).
    run = client.post("/controls/Finance.AP.1/run", follow_redirects=False)
    assert run.status_code != 500
