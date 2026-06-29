# tests/store/test_repo_sources.py
from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.migrations import migrate


def _db(tmp_path):
    conn = connect(tmp_path)
    migrate(conn)
    return conn


def test_upsert_and_get_project(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_project(conn, name="Acme", framework="nist", system={"name": "GSS"})
    p = repo.get_project(conn)
    assert p["name"] == "Acme"
    assert p["framework"] == "nist"
    assert p["system"] == {"name": "GSS"}  # JSON-decoded


def test_source_with_columns_roundtrip(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_source(
        conn, id="users", format="csv", path="data/users.csv",
        key_config={"mode": "single", "columns": ["user_id"]},
    )
    repo.set_columns(conn, "users", [
        {"original_name": "user_id", "display_name": "User ID",
         "data_type": "text", "is_key": True, "include": True, "ordinal": 0},
        {"original_name": "can_create", "display_name": "Can Create",
         "data_type": "boolean", "is_key": False, "include": True, "ordinal": 1},
    ])
    src = repo.get_source(conn, "users")
    assert src["format"] == "csv"
    assert src["key_config"] == {"mode": "single", "columns": ["user_id"]}
    assert [c["original_name"] for c in src["columns"]] == ["user_id", "can_create"]
    assert src["columns"][0]["is_key"] is True


def test_set_columns_replaces(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_source(conn, id="s", format="csv", path="data/s.csv", key_config={})
    repo.set_columns(conn, "s", [{"original_name": "a", "display_name": "A",
        "data_type": "text", "is_key": False, "include": True, "ordinal": 0}])
    repo.set_columns(conn, "s", [{"original_name": "b", "display_name": "B",
        "data_type": "text", "is_key": False, "include": True, "ordinal": 0}])
    assert [c["original_name"] for c in repo.get_source(conn, "s")["columns"]] == ["b"]
