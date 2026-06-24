from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.upgrade.check import UpdateInfo
from controlflow_sdk.upgrade.detect import InstallMethod


def test_settings_hub_links_to_updates(client):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert 'href="/settings/updates"' in resp.text


def test_updates_page_renders_with_toggle_off_by_default(client):
    resp = client.get("/settings/updates")
    assert resp.status_code == 200
    assert "Check for updates" in resp.text
    # Unchecked by default.
    assert "check_on_launch" in resp.text


def test_toggle_persists_true(client):
    resp = client.post(
        "/settings/updates/toggle",
        data={"check_on_launch": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    conn = connect(client.app.state.project_root)
    assert repo.get_check_updates_on_launch(conn) is True
    conn.close()


def test_toggle_unchecked_persists_false(client):
    conn = connect(client.app.state.project_root)
    repo.set_check_updates_on_launch(conn, True)
    conn.close()
    # An unchecked checkbox submits no field at all.
    resp = client.post("/settings/updates/toggle", data={}, follow_redirects=False)
    assert resp.status_code == 303
    conn = connect(client.app.state.project_root)
    assert repo.get_check_updates_on_launch(conn) is False
    conn.close()


def test_check_now_returns_result_partial(client, monkeypatch):
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.detect_install",
        lambda: InstallMethod.PIP,
    )
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.check_for_update",
        lambda method: UpdateInfo(method, "0.1.0", "0.2.0", True, "Version 0.2.0 is available."),
    )
    resp = client.post("/settings/updates/check")
    assert resp.status_code == 200
    assert "0.2.0" in resp.text


def test_header_indicator_up_to_date_shows_hover_actions(client, monkeypatch):
    conn = connect(client.app.state.project_root)
    repo.set_check_updates_on_launch(conn, True)
    conn.close()
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.detect_install",
        lambda: InstallMethod.PIP,
    )
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.check_for_update",
        lambda method: UpdateInfo(method, "0.1.0", "0.1.0", False, "You are up to date."),
    )
    resp = client.get("/updates/indicator")
    assert resp.status_code == 200
    assert "up-to-date" in resp.text
    assert "indicator-text" not in resp.text
    assert "You are up to date." not in resp.text
    assert 'hx-post="/updates/indicator/check"' in resp.text
    assert 'hx-target="#header-update-indicator"' in resp.text
    assert 'hx-post="/updates/indicator/check"' in resp.text
    assert "Check now" in resp.text


def test_header_indicator_update_available_shows_update_now_action(client, monkeypatch):
    conn = connect(client.app.state.project_root)
    repo.set_check_updates_on_launch(conn, True)
    conn.close()
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.detect_install",
        lambda: InstallMethod.PIP,
    )
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.check_for_update",
        lambda method: UpdateInfo(method, "0.1.0", "0.2.0", True, "Version 0.2.0 is available."),
    )
    resp = client.get("/updates/indicator")
    assert resp.status_code == 200
    assert "update-available" in resp.text
    assert "indicator-text" not in resp.text
    assert "New version" not in resp.text
    assert "aria-label=" in resp.text
    assert 'hx-post="/updates/indicator/check"' in resp.text
    assert 'hx-target="#header-update-indicator"' in resp.text
    assert 'hx-post="/upgrade"' in resp.text
    assert "Update now" in resp.text


def test_base_template_polls_header_indicator_every_two_minutes(client):
    page = client.get("/settings")
    assert page.status_code == 200
    assert "/updates/indicator/check" in page.text
    assert "120000" in page.text


def test_refresh_indicator_skips_check_when_toggle_off(client, monkeypatch):
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.check_for_update",
        lambda method: (_ for _ in ()).throw(AssertionError("unexpected network check")),
    )
    resp = client.post("/updates/indicator/check")
    assert resp.status_code == 200
    assert resp.text == ""
