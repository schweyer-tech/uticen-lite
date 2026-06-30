# tests/store/test_source_fetch_repo.py
from __future__ import annotations

from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.migrations import migrate


def _db(tmp_path):
    conn = connect(tmp_path)
    migrate(conn)
    return conn


def test_upsert_source_persists_sheet(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_source(
        conn, id="gl", format="xlsx", path="data/gl.xlsx", key_config={"mode": "auto"}, sheet="Q1"
    )
    assert repo.get_source(conn, "gl")["sheet"] == "Q1"
    conn.close()


def test_source_fetch_roundtrip_and_upsert(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_source(
        conn, id="api", format="csv", path="data/api.csv", key_config={"mode": "auto"}
    )
    repo.upsert_source_fetch(
        conn,
        source_id="api",
        url="https://x/y.json",
        headers={"Authorization": "Bearer t"},
        record_path="data.items",
        last_fetched_at="20260622T0000Z",
    )
    got = repo.get_source_fetch(conn, "api")
    assert got["url"] == "https://x/y.json"
    assert got["headers"] == {"Authorization": "Bearer t"}
    assert got["record_path"] == "data.items"
    # upsert overwrites
    repo.upsert_source_fetch(conn, source_id="api", url="https://x/z.json")
    got2 = repo.get_source_fetch(conn, "api")
    assert got2["url"] == "https://x/z.json" and got2["headers"] == {}
    assert repo.get_source_fetch(conn, "missing") is None
    conn.close()
