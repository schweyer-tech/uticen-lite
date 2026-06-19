from pathlib import Path

from controlflow_sdk.store.db import connect
from controlflow_sdk.store.migrations import SCHEMA_VERSION, migrate


def _user_version(conn) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def test_migrate_creates_all_tables_and_sets_version(tmp_path: Path):
    conn = connect(tmp_path)
    migrate(conn)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "project", "sources", "columns", "controls",
        "control_sources", "runs", "violations",
    } <= tables
    assert _user_version(conn) == SCHEMA_VERSION


def test_migrate_is_idempotent(tmp_path: Path):
    conn = connect(tmp_path)
    migrate(conn)
    migrate(conn)  # second run must be a no-op, not raise
    assert _user_version(conn) == SCHEMA_VERSION
