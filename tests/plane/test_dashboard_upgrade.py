from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.upgrade.check import UpdateInfo
from controlflow_sdk.upgrade.detect import InstallMethod


def _enable_check(client):
    conn = connect(client.app.state.project_root)
    repo.set_check_updates_on_launch(conn, True)
    conn.close()


def test_badge_empty_when_toggle_off(client, monkeypatch):
    # Even if a check WOULD find an update, OFF means no badge and no network.
    called = {"n": 0}

    def boom(method):
        called["n"] += 1
        return UpdateInfo(method, "0.1.0", "0.2.0", True, "x")

    monkeypatch.setattr("controlflow_sdk.plane.routes.updates.check_for_update", boom)
    resp = client.get("/updates/badge")
    assert resp.status_code == 200
    assert resp.text.strip() == ""
    assert called["n"] == 0  # no check ran while OFF


def test_badge_shows_when_on_and_newer(client, monkeypatch):
    _enable_check(client)
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.detect_install",
        lambda: InstallMethod.PIP,
    )
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.check_for_update",
        lambda method: UpdateInfo(method, "0.1.0", "0.2.0", True, "Version 0.2.0 is available."),
    )
    resp = client.get("/updates/badge")
    assert resp.status_code == 200
    assert "0.2.0" in resp.text
    assert "/upgrade" in resp.text


def test_upgrade_spawns_and_renders_upgrading(client, monkeypatch):
    spawned = {}
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.detect_install",
        lambda: InstallMethod.PIP,
    )
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.build_upgrade_command",
        lambda method, source_dir=None: [["pip", "install", "-U", "controlflow-sdk"]],
    )
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.spawn_detached_upgrade",
        lambda root, commands, current: spawned.update(commands=commands) or None,
    )
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.schedule_shutdown",
        lambda: spawned.update(shutdown=True),
    )
    resp = client.post("/upgrade")
    assert resp.status_code == 200
    assert "Upgrading" in resp.text
    assert spawned["commands"] == [["pip", "install", "-U", "controlflow-sdk"]]
    assert spawned["shutdown"] is True


def test_upgrade_unknown_renders_instructions(client, monkeypatch):
    monkeypatch.setattr(
        "controlflow_sdk.plane.routes.updates.detect_install",
        lambda: InstallMethod.UNKNOWN,
    )
    resp = client.post("/upgrade")
    assert resp.status_code == 200
    assert "pip install" in resp.text.lower() or "pipx" in resp.text.lower()


def test_dashboard_shows_post_upgrade_notice(client):
    from controlflow_sdk.upgrade.spawn import write_status

    write_status(client.app.state.project_root, {"ok": True, "from": "0.1.0"})
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Upgraded" in resp.text
    # One-shot: the notice clears after being shown once.
    assert "Upgraded" not in client.get("/").text
