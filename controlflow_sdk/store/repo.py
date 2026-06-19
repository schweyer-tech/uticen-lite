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


# ---- controls + bindings ---------------------------------------------------
def upsert_control(
    conn: sqlite3.Connection, *, id: str, title: str, objective: str, narrative: str,
    framework_refs: dict, test_kind: str, rule_spec: dict | None = None,
    test_code: str | None = None, failure_threshold_pct: float | None = None,
    failure_threshold_count: int | None = None, created_at: str = "", updated_at: str = "",
) -> None:
    conn.execute(
        """INSERT INTO controls
             (id, title, objective, narrative, framework_refs,
              failure_threshold_pct, failure_threshold_count,
              test_kind, rule_spec, test_code, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             title=excluded.title, objective=excluded.objective,
             narrative=excluded.narrative, framework_refs=excluded.framework_refs,
             failure_threshold_pct=excluded.failure_threshold_pct,
             failure_threshold_count=excluded.failure_threshold_count,
             test_kind=excluded.test_kind, rule_spec=excluded.rule_spec,
             test_code=excluded.test_code, updated_at=excluded.updated_at""",
        (id, title, objective, narrative, json.dumps(framework_refs),
         failure_threshold_pct, failure_threshold_count, test_kind,
         json.dumps(rule_spec) if rule_spec is not None else None,
         test_code, created_at, updated_at),
    )
    conn.commit()


def set_control_sources(conn: sqlite3.Connection, control_id: str, source_ids: list[str]) -> None:
    conn.execute("DELETE FROM control_sources WHERE control_id = ?", (control_id,))
    conn.executemany(
        "INSERT INTO control_sources (control_id, source_id, ordinal) VALUES (?, ?, ?)",
        [(control_id, sid, i) for i, sid in enumerate(source_ids)],
    )
    conn.commit()


def _source_ids_for(conn: sqlite3.Connection, control_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT source_id FROM control_sources WHERE control_id = ? ORDER BY ordinal",
        (control_id,),
    ).fetchall()
    return [r["source_id"] for r in rows]


def get_control(conn: sqlite3.Connection, control_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM controls WHERE id = ?", (control_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["framework_refs"] = _loads(d.get("framework_refs"), {})
    d["rule_spec"] = _loads(d.get("rule_spec"), None)
    d["source_ids"] = _source_ids_for(conn, control_id)
    return d


def list_controls(conn: sqlite3.Connection) -> list[dict]:
    ids = [r["id"] for r in conn.execute("SELECT id FROM controls ORDER BY id").fetchall()]
    controls = []
    for cid in ids:
        ctrl = get_control(conn, cid)
        if ctrl is not None:
            controls.append(ctrl)
    return controls
