# controlflow_sdk/store/repo.py
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from typing import Any

from controlflow_sdk.model.run import RunRecord


def _loads(value: str | None, fallback: Any) -> Any:
    return json.loads(value) if value else fallback


def _list_by_getter(
    conn: sqlite3.Connection,
    query: str,
    getter: Callable[[sqlite3.Connection, str], dict | None],
    *,
    id_column: str = "id",
    params: Sequence[Any] = (),
) -> list[dict]:
    """Run ``query`` to collect ids, fetch each via ``getter``, drop None results."""
    ids = [r[id_column] for r in conn.execute(query, params).fetchall()]
    items = []
    for item_id in ids:
        item = getter(conn, item_id)
        if item is not None:
            items.append(item)
    return items


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


def get_check_updates_on_launch(conn: sqlite3.Connection) -> bool:
    """Whether the control plane checks for a newer version on launch (default False)."""
    project = get_project(conn) or {}
    system = project.get("system") or {}
    return bool(system.get("check_updates_on_launch", False))


def set_check_updates_on_launch(conn: sqlite3.Connection, value: bool) -> None:
    """Persist the opt-in update-check toggle, preserving the rest of the project record."""
    project = get_project(conn) or {}
    system = dict(project.get("system") or {})
    system["check_updates_on_launch"] = bool(value)
    upsert_project(
        conn,
        name=project.get("name", "") or "",
        framework=project.get("framework"),
        system=system,
        created_at=project.get("created_at", "") or "",
    )


# ---- sources + columns -----------------------------------------------------
def upsert_source(
    conn: sqlite3.Connection, *, id: str, format: str, path: str,
    key_config: dict, title: str | None = None, description: str | None = None,
    completeness_accuracy: str | None = None, extract_date: str | None = None,
    created_at: str = "", sheet: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO sources
             (id, format, path, key_config, title, description,
              completeness_accuracy, extract_date, created_at, sheet)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             format=excluded.format, path=excluded.path,
             key_config=excluded.key_config, title=excluded.title,
             description=excluded.description,
             completeness_accuracy=excluded.completeness_accuracy,
             extract_date=excluded.extract_date, sheet=excluded.sheet""",
        (id, format, path, json.dumps(key_config), title, description,
         completeness_accuracy, extract_date, created_at, sheet),
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
    return _list_by_getter(conn, "SELECT id FROM sources ORDER BY id", get_source)


# ---- source files (per-file data lineage) ----------------------------------
def _insert_current_file(
    conn: sqlite3.Connection, *, source_id: str, stored_path: str,
    original_name: str, as_of_date: str | None, row_count: int | None,
    uploaded_at: str,
) -> None:
    conn.execute(
        """INSERT INTO source_files
             (source_id, stored_path, original_name, as_of_date, row_count,
              uploaded_at, is_current)
           VALUES (?, ?, ?, ?, ?, ?, 1)""",
        (source_id, stored_path, original_name, as_of_date, row_count, uploaded_at),
    )


def set_initial_file(
    conn: sqlite3.Connection, *, source_id: str, stored_path: str,
    original_name: str, as_of_date: str | None, row_count: int | None,
    uploaded_at: str = "",
) -> None:
    """Replace all file rows for a source with one current row (import/create)."""
    conn.execute("DELETE FROM source_files WHERE source_id = ?", (source_id,))
    _insert_current_file(conn, source_id=source_id, stored_path=stored_path,
                         original_name=original_name, as_of_date=as_of_date,
                         row_count=row_count, uploaded_at=uploaded_at)
    conn.commit()


def record_current_file(
    conn: sqlite3.Connection, *, source_id: str, stored_path: str,
    original_name: str, as_of_date: str | None, row_count: int | None,
    uploaded_at: str = "",
) -> None:
    """Demote any current row, then add a new current row (refresh)."""
    conn.execute(
        "UPDATE source_files SET is_current = 0 WHERE source_id = ? AND is_current = 1",
        (source_id,),
    )
    _insert_current_file(conn, source_id=source_id, stored_path=stored_path,
                         original_name=original_name, as_of_date=as_of_date,
                         row_count=row_count, uploaded_at=uploaded_at)
    conn.commit()


def archive_current_file(
    conn: sqlite3.Connection, source_id: str, new_stored_path: str
) -> None:
    conn.execute(
        "UPDATE source_files SET is_current = 0, stored_path = ? "
        "WHERE source_id = ? AND is_current = 1",
        (new_stored_path, source_id),
    )
    conn.commit()


def get_current_file(conn: sqlite3.Connection, source_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM source_files WHERE source_id = ? AND is_current = 1",
        (source_id,),
    ).fetchone()
    return dict(row) if row else None


def list_source_files(conn: sqlite3.Connection, source_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM source_files WHERE source_id = ? "
        "ORDER BY is_current DESC, uploaded_at DESC, id DESC",
        (source_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def set_current_file_asof(
    conn: sqlite3.Connection, source_id: str, as_of_date: str | None
) -> None:
    conn.execute(
        "UPDATE source_files SET as_of_date = ? WHERE source_id = ? AND is_current = 1",
        (as_of_date, source_id),
    )
    conn.execute("UPDATE sources SET extract_date = ? WHERE id = ?",
                 (as_of_date, source_id))
    conn.commit()


# ---- source fetch (URL-snapshot provenance; store/UI-only) ------------------
def upsert_source_fetch(
    conn: sqlite3.Connection, *, source_id: str, url: str,
    headers: dict | None = None, record_path: str | None = None,
    last_fetched_at: str | None = None,
) -> None:
    """Persist (or overwrite) the URL/headers/record_path for a fetched source.

    SECURITY: ``headers`` may contain auth tokens and is stored PLAINTEXT in
    controlplane.db. The UI warns the user. This row never enters the bundle.
    """
    conn.execute(
        """INSERT INTO source_fetch
             (source_id, url, headers, record_path, last_fetched_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(source_id) DO UPDATE SET
             url=excluded.url, headers=excluded.headers,
             record_path=excluded.record_path, last_fetched_at=excluded.last_fetched_at""",
        (source_id, url, json.dumps(headers or {}), record_path, last_fetched_at),
    )
    conn.commit()


def get_source_fetch(conn: sqlite3.Connection, source_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM source_fetch WHERE source_id = ?", (source_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["headers"] = _loads(d.get("headers"), {})
    return d


# ---- controls + bindings ---------------------------------------------------
def upsert_control(
    conn: sqlite3.Connection, *, id: str, title: str, objective: str, narrative: str,
    framework_refs: dict, test_kind: str, rule_spec: dict | None = None,
    test_code: str | None = None, pipeline: dict | None = None,
    failure_threshold_pct: float | None = None,
    failure_threshold_count: int | None = None,
    failure_threshold_rationale: str | None = None,
    created_at: str = "", updated_at: str = "",
) -> None:
    """Upsert a control.

    ``test_kind`` is ``rule`` | ``python`` | ``pipeline``. For a ``pipeline``
    control the *store-only* visual graph lands in the ``pipeline`` column while
    its COMPILED artifact still lands in ``rule_spec``/``test_code`` (so the
    runner/bundle reuse the existing paths and the bundle never sees the graph).
    """
    conn.execute(
        """INSERT INTO controls
             (id, title, objective, narrative, framework_refs,
              failure_threshold_pct, failure_threshold_count,
              failure_threshold_rationale,
              test_kind, rule_spec, test_code, pipeline, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             title=excluded.title, objective=excluded.objective,
             narrative=excluded.narrative, framework_refs=excluded.framework_refs,
             failure_threshold_pct=excluded.failure_threshold_pct,
             failure_threshold_count=excluded.failure_threshold_count,
             failure_threshold_rationale=excluded.failure_threshold_rationale,
             test_kind=excluded.test_kind, rule_spec=excluded.rule_spec,
             test_code=excluded.test_code, pipeline=excluded.pipeline,
             updated_at=excluded.updated_at""",
        (id, title, objective, narrative, json.dumps(framework_refs),
         failure_threshold_pct, failure_threshold_count, failure_threshold_rationale,
         test_kind,
         json.dumps(rule_spec) if rule_spec is not None else None,
         test_code, json.dumps(pipeline) if pipeline is not None else None,
         created_at, updated_at),
    )
    conn.commit()


def set_control_sources(conn: sqlite3.Connection, control_id: str, source_ids: list[str]) -> None:
    conn.execute("DELETE FROM control_sources WHERE control_id = ?", (control_id,))
    conn.executemany(
        "INSERT INTO control_sources (control_id, source_id, ordinal) VALUES (?, ?, ?)",
        [(control_id, sid, i) for i, sid in enumerate(source_ids)],
    )
    conn.commit()


def rename_control_id(conn: sqlite3.Connection, current_id: str, new_id: str) -> None:
    """Rename a control id while preserving source bindings and runs."""
    current_id = current_id.strip()
    new_id = new_id.strip()
    if not new_id:
        raise ValueError("Control ID is required.")
    if current_id == new_id:
        return
    if get_control(conn, current_id) is None:
        raise ValueError(f"Control {current_id!r} does not exist.")
    if get_control(conn, new_id) is not None:
        raise ValueError(f"Control ID {new_id!r} already exists.")

    row = conn.execute("SELECT * FROM controls WHERE id = ?", (current_id,)).fetchone()
    if row is None:
        raise ValueError(f"Control {current_id!r} does not exist.")
    control = dict(row)

    try:
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO controls
                 (id, title, objective, narrative, framework_refs,
                  failure_threshold_pct, failure_threshold_count,
                  failure_threshold_rationale,
                  test_kind, rule_spec, test_code, pipeline, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id,
                control["title"],
                control["objective"],
                control["narrative"],
                control["framework_refs"],
                control["failure_threshold_pct"],
                control["failure_threshold_count"],
                control["failure_threshold_rationale"],
                control["test_kind"],
                control["rule_spec"],
                control["test_code"],
                control["pipeline"],
                control["created_at"],
                control["updated_at"],
            ),
        )
        conn.execute(
            "UPDATE control_sources SET control_id = ? WHERE control_id = ?",
            (new_id, current_id),
        )
        conn.execute(
            "UPDATE runs SET control_id = ? WHERE control_id = ?",
            (new_id, current_id),
        )
        conn.execute("DELETE FROM controls WHERE id = ?", (current_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


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
    d["pipeline"] = _loads(d.get("pipeline"), None)
    d["source_ids"] = _source_ids_for(conn, control_id)
    return d


def list_controls(conn: sqlite3.Connection) -> list[dict]:
    return _list_by_getter(conn, "SELECT id FROM controls ORDER BY id", get_control)


# ---- runs + violations -----------------------------------------------------
def insert_run(conn: sqlite3.Connection, run: RunRecord) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO runs
             (run_id, control_id, executed_at, population_size,
              total, passed, failed, pass_rate, provenance, created_at,
              procedure_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run.run_id, run.control_id, run.executed_at, run.population_size,
         run.population_size, run.passed, run.failed, run.pass_rate,
         json.dumps([p.to_dict() for p in run.provenance]), run.executed_at,
         run.procedure_id),
    )
    conn.execute("DELETE FROM violations WHERE run_id = ?", (run.run_id,))
    conn.executemany(
        """INSERT INTO violations (run_id, item_key, description, severity, details)
           VALUES (?, ?, ?, ?, ?)""",
        [(run.run_id, v.item_key, v.description, str(v.severity), json.dumps(v.details))
         for v in run.violations],
    )
    conn.commit()


def _violations_for(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT item_key, description, severity, details FROM violations "
        "WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["details"] = _loads(d.get("details"), {})
        out.append(d)
    return out


def get_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["provenance"] = _loads(d.get("provenance"), [])
    d["violations"] = _violations_for(conn, run_id)
    return d


def list_runs_for(conn: sqlite3.Connection, control_id: str) -> list[dict]:
    return _list_by_getter(
        conn,
        "SELECT run_id FROM runs WHERE control_id = ? "
        "ORDER BY executed_at DESC, created_at DESC",
        get_run,
        id_column="run_id",
        params=(control_id,),
    )


def latest_run(conn: sqlite3.Connection, control_id: str) -> dict | None:
    runs = list_runs_for(conn, control_id)
    return runs[0] if runs else None
