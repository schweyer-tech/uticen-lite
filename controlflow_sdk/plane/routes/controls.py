from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect


def _rule_spec_from_form(form: Any) -> dict[str, Any]:
    return {
        "logic": form.get("rule_logic", "all"),
        "conditions": [],
        "severity": form.get("rule_severity", "medium"),
        "description_template": form.get("rule_description", ""),
        "item_key_column": form.get("rule_item_key") or None,
    }


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
    repo.set_control_sources(conn, cid, form.getlist("source_ids"))
    return cid


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    @app.get("/controls/new", response_class=HTMLResponse)
    def new_control(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        return templates.TemplateResponse(
            request,
            "control_edit.html",
            {
                "project": repo.get_project(conn) or {"name": ""},
                "control": None,
                "sources": repo.list_sources(conn),
            },
        )

    @app.get("/controls/{control_id}", response_class=HTMLResponse)
    def edit_control(
        control_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        return templates.TemplateResponse(
            request,
            "control_edit.html",
            {
                "project": repo.get_project(conn) or {"name": ""},
                "control": repo.get_control(conn, control_id),
                "sources": repo.list_sources(conn),
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
