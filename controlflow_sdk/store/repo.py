# controlflow_sdk/store/repo.py
from __future__ import annotations

import json
import sqlite3
from typing import Any


def _loads(value: str | None, fallback: Any) -> Any:
    return json.loads(value) if value else fallback


# ---- project ---------------------------------------------------------------
def upsert_project(
    conn: sqlite3.Connection, *, name: str, framework: str | None = None,
    system: dict | None = None, created_at: str = "",
) -> None:
    conn.execute(
        """INSERT INTO project (id, name, framework, system, created_at)
           VALUES (1, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, framework=excluded.framework,
             system=excluded.system""",
        (name, framework, json.dumps(system) if system is not None else None, created_at),
    )
    conn.commit()


def get_project(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM project WHERE id = 1").fetchone()
    if row is None:
        return None
    d = dict(row)
    d["system"] = _loads(d.get("system"), {})
    return d


# ---- sources + columns -----------------------------------------------------
def upsert_source(
    conn: sqlite3.Connection, *, id: str, format: str, path: str,
    key_config: dict, description: str | None = None,
    completeness_accuracy: str | None = None, extract_date: str | None = None,
    created_at: str = "",
) -> None:
    conn.execute(
        """INSERT INTO sources
             (id, format, path, key_config, description,
              completeness_accuracy, extract_date, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             format=excluded.format, path=excluded.path,
             key_config=excluded.key_config, description=excluded.description,
             completeness_accuracy=excluded.completeness_accuracy,
             extract_date=excluded.extract_date""",
        (id, format, path, json.dumps(key_config), description,
         completeness_accuracy, extract_date, created_at),
    )
    conn.commit()


def set_columns(conn: sqlite3.Connection, source_id: str, columns: list[dict]) -> None:
    conn.execute("DELETE FROM columns WHERE source_id = ?", (source_id,))
    conn.executemany(
        """INSERT INTO columns
             (source_id, original_name, display_name, data_type,
              is_key, include, ordinal)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (source_id, c["original_name"], c["display_name"], c.get("data_type", "text"),
             int(bool(c.get("is_key"))), int(bool(c.get("include", True))),
             int(c.get("ordinal", i)))
            for i, c in enumerate(columns)
        ],
    )
    conn.commit()


def _columns_for(conn: sqlite3.Connection, source_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM columns WHERE source_id = ? ORDER BY ordinal", (source_id,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["is_key"] = bool(d["is_key"])
        d["include"] = bool(d["include"])
        out.append(d)
    return out


def get_source(conn: sqlite3.Connection, source_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["key_config"] = _loads(d.get("key_config"), {})
    d["columns"] = _columns_for(conn, source_id)
    return d


def list_sources(conn: sqlite3.Connection) -> list[dict]:
    ids = [r["id"] for r in conn.execute("SELECT id FROM sources ORDER BY id").fetchall()]
    sources = []
    for sid in ids:
        src = get_source(conn, sid)
        if src is not None:
            sources.append(src)
    return sources
