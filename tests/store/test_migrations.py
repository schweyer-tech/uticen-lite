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


def _source_columns(conn) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(sources)").fetchall()}


def test_sources_has_title_column(tmp_path: Path):
    conn = connect(tmp_path)
    migrate(conn)
    assert "title" in _source_columns(conn)


def test_v1_store_upgrades_to_title_without_data_loss(tmp_path: Path):
    # Simulate a store left at schema v1 (before the title column existed).
    from controlflow_sdk.store.migrations import _STEPS

    conn = connect(tmp_path)
    conn.executescript(_STEPS[0])
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO sources (id, format, path, key_config) VALUES ('s', 'csv', 'data/s.csv', '{}')"
    )
    conn.commit()
    assert "title" not in _source_columns(conn)

    migrate(conn)  # forward-only step 2 adds the column on the existing DB
    assert _user_version(conn) == SCHEMA_VERSION
    assert "title" in _source_columns(conn)
    row = conn.execute("SELECT id, title FROM sources WHERE id = 's'").fetchone()
    assert row[0] == "s" and row[1] is None


def test_source_files_table_and_backfill(tmp_path: Path):
    from controlflow_sdk.store.migrations import _STEPS

    conn = connect(tmp_path)
    conn.executescript(_STEPS[0])      # v1 schema
    conn.executescript(_STEPS[1])      # v2: title column
    conn.execute("PRAGMA user_version = 2")
    conn.execute(
        "INSERT INTO sources (id, format, path, key_config, extract_date, created_at) "
        "VALUES ('s', 'csv', 'data/s.csv', '{}', '2026-03-31', '2026-01-01')"
    )
    conn.commit()

    migrate(conn)  # forward step 3 adds the table + backfills a current row
    assert _user_version(conn) == SCHEMA_VERSION
    cols = {r[1] for r in conn.execute("PRAGMA table_info(source_files)").fetchall()}
    assert {"source_id", "stored_path", "original_name", "as_of_date",
            "row_count", "uploaded_at", "is_current"} <= cols
    row = conn.execute(
        "SELECT source_id, stored_path, original_name, as_of_date, is_current "
        "FROM source_files WHERE source_id = 's'"
    ).fetchone()
    assert tuple(row) == ("s", "data/s.csv", "s.csv", "2026-03-31", 1)
