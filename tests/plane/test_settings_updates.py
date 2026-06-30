from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.upgrade.check import UpdateInfo
from uticen_lite.upgrade.detect import InstallMethod


def test_settings_hub_links_to_updates(client):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert 'href="/settings/updates"' in resp.text


def test_updates_page_renders_with_toggle_on_by_default(client):
    resp = client.get("/settings/updates")
    assert resp.status_code == 200
    assert "Check for updates" in resp.text
    # Checked by default — the launch check / header indicator are on out of the box.
    assert "check_on_launch" in resp.text
    assert "checked" in resp.text


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
        "uticen_lite.plane.routes.updates.detect_install",
        lambda: InstallMethod.PIP,
    )
    monkeypatch.setattr(
        "uticen_lite.plane.routes.updates.check_for_update",
        lambda method: UpdateInfo(method, "0.1.0", "0.2.0", True, "Version 0.2.0 is available."),
    )
    resp = client.post("/settings/updates/check")
    assert resp.status_code == 200
    assert "0.2.0" in resp.text


def test_header_indicator_up_to_date_shows_tooltip_and_modal_trigger(client, monkeypatch):
    conn = connect(client.app.state.project_root)
    repo.set_check_updates_on_launch(conn, True)
    conn.close()
    monkeypatch.setattr(
        "uticen_lite.plane.routes.updates.detect_install",
        lambda: InstallMethod.PIP,
    )
    monkeypatch.setattr(
        "uticen_lite.plane.routes.updates.check_for_update",
        lambda method: UpdateInfo(method, "0.1.0", "0.1.0", False, "You are up to date."),
    )
    resp = client.get("/updates/indicator")
    assert resp.status_code == 200
    assert "up-to-date" in resp.text
    assert "indicator-text" not in resp.text
    assert 'title="Up to date: 0.1.0"' in resp.text
    assert "data-update-modal-open" in resp.text
    assert "update-popover" not in resp.text
    assert "update-modal-template" in resp.text
    assert 'hx-post="/updates/indicator/check"' not in resp.text
    assert "Check now" not in resp.text


def test_header_indicator_update_available_shows_update_now_in_modal(client, monkeypatch):
    conn = connect(client.app.state.project_root)
    repo.set_check_updates_on_launch(conn, True)
    conn.close()
    monkeypatch.setattr(
        "uticen_lite.plane.routes.updates.detect_install",
        lambda: InstallMethod.PIP,
    )
    monkeypatch.setattr(
        "uticen_lite.plane.routes.updates.check_for_update",
        lambda method: UpdateInfo(method, "0.1.0", "0.2.0", True, "Version 0.2.0 is available."),
    )
    resp = client.get("/updates/indicator")
    assert resp.status_code == 200
    assert "update-available" in resp.text
    assert "indicator-text" not in resp.text
    assert 'title="Update available: 0.2.0"' in resp.text
    assert "data-update-modal-open" in resp.text
    assert "update-popover" not in resp.text
    assert "update-modal-template" in resp.text
    assert 'hx-post="/updates/indicator/check"' not in resp.text
    assert 'hx-post="/upgrade"' in resp.text
    assert "Update now" in resp.text


def test_base_template_polls_header_indicator_every_two_minutes(client):
    page = client.get("/settings")
    assert page.status_code == 200
    assert "/updates/indicator/check" in page.text
    assert "120000" in page.text
    assert "update-modal" in page.text
    assert "update-modal-content" in page.text


def test_refresh_indicator_skips_check_when_toggle_off(client, monkeypatch):
    # The toggle is ON by default, so turn it OFF to exercise the zero-egress path.
    conn = connect(client.app.state.project_root)
    repo.set_check_updates_on_launch(conn, False)
    conn.close()
    monkeypatch.setattr(
        "uticen_lite.plane.routes.updates.check_for_update",
        lambda method: (_ for _ in ()).throw(AssertionError("unexpected network check")),
    )
    resp = client.post("/updates/indicator/check")
    assert resp.status_code == 200
    assert resp.text == ""
