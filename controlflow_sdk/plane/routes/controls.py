from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect


def _typed(value: str) -> Any:
    v = value.strip()
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except ValueError:
        return v


def _primary_columns(conn: sqlite3.Connection, source_ids: list[str]) -> list[dict]:
    """Columns of the rule's primary population (the first bound source).

    The first bound source is the primary population (mirrors
    ``runner/execute.py`` which uses ``populations[0]``). Returns ``[]`` when no
    source is bound or the source is missing, so the template falls back to a
    free-text column input.
    """
    if not source_ids:
        return []
    src = repo.get_source(conn, source_ids[0])
    return src["columns"] if src else []


def _padded(items: list, n: int) -> list:
    """Right-pad a getlist to length ``n`` with empty strings (parallel fields)."""
    return list(items) + [""] * (n - len(items))


def _resolve_column(selected: str, freetext: str) -> str:
    """Resolve the posted column: the dropdown value, or the free-text sibling
    when the user picked the ``__other__`` (type-a-name) escape hatch."""
    if selected == "__other__" and freetext.strip():
        return freetext.strip()
    return selected.strip()


def _rule_spec_from_form(form: Any) -> dict[str, Any]:
    columns = form.getlist("cond_column")
    n = len(columns)
    ops = _padded(form.getlist("cond_op"), n)
    values = _padded(form.getlist("cond_value"), n)
    freetexts = _padded(form.getlist("cond_column_freetext"), n)
    other_sources = _padded(form.getlist("cond_other_source"), n)
    this_keys = _padded(form.getlist("cond_this_key"), n)
    other_keys = _padded(form.getlist("cond_other_key"), n)
    conditions: list[dict[str, Any]] = []
    for i, (col, op, raw) in enumerate(zip(columns, ops, values)):
        if op in ("exists_in", "not_exists_in"):
            this_key = this_keys[i].strip()
            if not this_key:
                continue
            conditions.append({
                "op": op,
                "column": this_key,
                "other_source": other_sources[i].strip(),
                "this_key": this_key,
                "other_key": other_keys[i].strip(),
            })
            continue
        resolved = _resolve_column(col, freetexts[i])
        if not resolved:
            continue
        cond: dict[str, Any] = {"column": resolved, "op": op}
        if op in ("is_empty", "not_empty", "is_duplicate"):
            pass
        elif op in ("in", "not_in"):
            cond["value"] = [_typed(p) for p in raw.split("|") if p.strip()]
        else:
            cond["value"] = _typed(raw)
        conditions.append(cond)
    return {
        "logic": form.get("rule_logic", "all"),
        "conditions": conditions,
        "severity": form.get("rule_severity", "medium"),
        "description_template": form.get("rule_description", ""),
        "item_key_column": form.get("rule_item_key") or None,
    }


def _cross_source_ids(rule_spec: dict[str, Any] | None) -> list[str]:
    """The set of source ids referenced by cross-source conditions (source B)."""
    if not rule_spec:
        return []
    out: list[str] = []
    for c in rule_spec.get("conditions", []):
        other = c.get("other_source")
        if other and other not in out:
            out.append(other)
    return out


def _save_from_form(conn: sqlite3.Connection, form: Any) -> str:
    cid = str(form.get("id")).strip()
    nist = [s.strip() for s in str(form.get("framework_nist", "")).split(",") if s.strip()]
    test_kind = form.get("test_kind", "rule")
    rule_spec = _rule_spec_from_form(form) if test_kind == "rule" else None
    test_code = form.get("test_code") if test_kind == "python" else None
    pct = form.get("failure_threshold_pct")
    cnt = form.get("failure_threshold_count")
    repo.upsert_control(
        conn,
        id=cid,
        title=form.get("title", ""),
        objective=form.get("objective", ""),
        narrative=form.get("narrative", ""),
        framework_refs={"nist": nist},
        test_kind=test_kind,
        rule_spec=rule_spec,
        test_code=test_code,
        failure_threshold_pct=float(pct) if pct else None,
        failure_threshold_count=int(cnt) if cnt else None,
    )
    # Auto-bind every source B referenced by a cross-source condition so the
    # runner can load it — the analyst need not also tick B's checkbox.
    source_ids = list(form.getlist("source_ids"))
    for sid in _cross_source_ids(rule_spec):
        if sid not in source_ids:
            source_ids.append(sid)
    repo.set_control_sources(conn, cid, source_ids)
    return cid


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    @app.get("/controls/_condition_row", response_class=HTMLResponse)
    def condition_row(
        request: Request,
        source_id: str = "",
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        cols = _primary_columns(conn, [source_id]) if source_id else []
        return templates.TemplateResponse(
            request, "partials/rule_condition.html",
            {"columns": cols, "all_sources": repo.list_sources(conn)},
        )

    @app.get("/controls/new", response_class=HTMLResponse)
    def new_control(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        from controlflow_sdk.plane.routes.ai import _ai_configured

        return templates.TemplateResponse(
            request,
            "control_edit.html",
            {
                "project": repo.get_project(conn) or {"name": ""},
                "control": None,
                "sources": repo.list_sources(conn),
                "columns": [],  # no bound source yet → free-text fallback
                "all_sources": repo.list_sources(conn),
                "ai_enabled": _ai_configured(conn),
            },
        )

    @app.get("/controls/{control_id}", response_class=HTMLResponse)
    def edit_control(
        control_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        from controlflow_sdk.plane.routes.ai import _ai_configured

        control = repo.get_control(conn, control_id)
        return templates.TemplateResponse(
            request,
            "control_edit.html",
            {
                "project": repo.get_project(conn) or {"name": ""},
                "control": control,
                "sources": repo.list_sources(conn),
                "columns": _primary_columns(conn, control["source_ids"]) if control else [],
                "all_sources": repo.list_sources(conn),
                "ai_enabled": _ai_configured(conn),
            },
        )

    @app.post("/controls")
    async def create_control(request: Request) -> Any:
        root = request.app.state.project_root
        conn = connect(root)
        try:
            form = await request.form()
            cid = _save_from_form(conn, form)
            return RedirectResponse(f"/controls/{cid}", status_code=303)
        finally:
            conn.close()

    @app.post("/controls/{control_id}")
    async def update_control(control_id: str, request: Request) -> Any:
        root = request.app.state.project_root
        conn = connect(root)
        try:
            form = await request.form()
            _save_from_form(conn, form)
            return RedirectResponse(f"/controls/{control_id}", status_code=303)
        finally:
            conn.close()
