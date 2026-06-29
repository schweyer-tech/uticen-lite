from __future__ import annotations

from uticen_lite.store.db import connect
from uticen_lite.store.migrations import migrate


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_step6_adds_sheet_and_source_fetch(tmp_path):
    conn = connect(tmp_path)
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 6
    assert "sheet" in _cols(conn, "sources")
    assert _cols(conn, "source_fetch") == {
        "source_id", "url", "headers", "record_path", "last_fetched_at"
    }
    # idempotent
    migrate(conn)
    assert "sheet" in _cols(conn, "sources")
    conn.close()
