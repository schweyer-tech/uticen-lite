from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

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
