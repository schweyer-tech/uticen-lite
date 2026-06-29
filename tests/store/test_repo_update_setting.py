from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.migrations import migrate


def _db(tmp_path):
    conn = connect(tmp_path)
    migrate(conn)
    return conn


def test_default_is_true(tmp_path):
    # On by default so the header update indicator shows out of the box; OFF is
    # the explicit opt-in to strict zero egress.
    conn = _db(tmp_path)
    repo.upsert_project(conn, name="Acme")
    assert repo.get_check_updates_on_launch(conn) is True


def test_explicit_false_is_respected(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_project(conn, name="Acme")
    repo.set_check_updates_on_launch(conn, False)
    assert repo.get_check_updates_on_launch(conn) is False


def test_set_then_get_true(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_project(conn, name="Acme")
    repo.set_check_updates_on_launch(conn, True)
    assert repo.get_check_updates_on_launch(conn) is True


def test_toggle_preserves_other_system_keys(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_project(conn, name="Acme", system={"ai": {"provider": "openai"}})
    repo.set_check_updates_on_launch(conn, True)
    project = repo.get_project(conn)
    assert project["system"]["ai"] == {"provider": "openai"}
    assert project["system"]["check_updates_on_launch"] is True
    assert project["name"] == "Acme"
