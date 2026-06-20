from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 3

# Forward-only, idempotent DDL. Index = target user_version.
_STEPS: list[str] = [
    # --- step 1 -> user_version 1 -------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS project (
        id            INTEGER PRIMARY KEY CHECK (id = 1),
        name          TEXT NOT NULL DEFAULT '',
        framework     TEXT,
        system        TEXT,           -- JSON
        created_at    TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS sources (
        id            TEXT PRIMARY KEY,
        format        TEXT NOT NULL,  -- csv | parquet | xlsx
        path          TEXT NOT NULL,  -- relative, under data/
        key_config    TEXT NOT NULL DEFAULT '{}',  -- JSON
        description   TEXT,
        completeness_accuracy TEXT,
        extract_date  TEXT,
        created_at    TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS columns (
        source_id     TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        original_name TEXT NOT NULL,
        display_name  TEXT NOT NULL,
        data_type     TEXT NOT NULL DEFAULT 'text',
        is_key        INTEGER NOT NULL DEFAULT 0,
        include       INTEGER NOT NULL DEFAULT 1,
        ordinal       INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (source_id, original_name)
    );
    CREATE TABLE IF NOT EXISTS controls (
        id            TEXT PRIMARY KEY,
        title         TEXT NOT NULL DEFAULT '',
        objective     TEXT NOT NULL DEFAULT '',
        narrative     TEXT NOT NULL DEFAULT '',
        framework_refs TEXT NOT NULL DEFAULT '{}',  -- JSON {nist:[...], extra:{...}}
        failure_threshold_pct   REAL,
        failure_threshold_count INTEGER,
        test_kind     TEXT NOT NULL DEFAULT 'rule',  -- rule | python
        rule_spec     TEXT,            -- JSON when test_kind=rule
        test_code     TEXT,            -- text when test_kind=python
        created_at    TEXT NOT NULL DEFAULT '',
        updated_at    TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS control_sources (
        control_id    TEXT NOT NULL REFERENCES controls(id) ON DELETE CASCADE,
        source_id     TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        ordinal       INTEGER NOT NULL DEFAULT 0,  -- 0 = primary
        PRIMARY KEY (control_id, source_id)
    );
    CREATE TABLE IF NOT EXISTS runs (
        run_id          TEXT PRIMARY KEY,
        control_id      TEXT NOT NULL REFERENCES controls(id) ON DELETE CASCADE,
        executed_at     TEXT NOT NULL,
        population_size INTEGER NOT NULL DEFAULT 0,
        total           INTEGER NOT NULL DEFAULT 0,
        passed          INTEGER NOT NULL DEFAULT 0,
        failed          INTEGER NOT NULL DEFAULT 0,
        pass_rate       REAL NOT NULL DEFAULT 0,
        provenance      TEXT NOT NULL DEFAULT '[]',  -- JSON list[SourceProvenance.to_dict()]
        created_at      TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS violations (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id        TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        item_key      TEXT NOT NULL,
        description   TEXT NOT NULL,
        severity      TEXT NOT NULL DEFAULT 'medium',
        details       TEXT NOT NULL DEFAULT '{}'  -- JSON
    );
    """,
    # --- step 2 -> user_version 2 -------------------------------------------
    # Author-facing display title for a source (shown in the source picker, the
    # sources list, and the source editor). Display/UI metadata only — it is NOT
    # carried into the export bundle (see SourceBinding.to_data_source()).
    """
    ALTER TABLE sources ADD COLUMN title TEXT;
    """,
    # --- step 3 -> user_version 3 -------------------------------------------
    # Per-file data lineage: one row per uploaded file version. is_current=1 is the
    # live file (its stored_path == sources.path); archived versions point under
    # data/.versions/<id>/. as_of_date is the file's data-as-of. Store/UI only — the
    # bundle path reads sources.extract_date (kept in sync with the current row).
    # Backfill one current row per existing source so single-file sources show history.
    """
    CREATE TABLE IF NOT EXISTS source_files (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id     TEXT NOT NULL,
        stored_path   TEXT NOT NULL,
        original_name TEXT NOT NULL,
        as_of_date    TEXT,
        row_count     INTEGER,
        uploaded_at   TEXT NOT NULL DEFAULT '',
        is_current    INTEGER NOT NULL DEFAULT 0
    );
    INSERT INTO source_files
        (source_id, stored_path, original_name, as_of_date, uploaded_at, is_current)
    SELECT id, path, replace(path, 'data/', ''), extract_date, created_at, 1
    FROM sources;
    """,
]


def migrate(conn: sqlite3.Connection) -> None:
    """Apply all forward steps beyond the DB's current user_version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for idx, ddl in enumerate(_STEPS, start=1):
        if idx <= current:
            continue
        conn.executescript(ddl)
        conn.execute(f"PRAGMA user_version = {idx}")
    conn.commit()
