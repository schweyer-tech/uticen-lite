from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.run_service import run_control_in_store


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    @app.post("/controls/{control_id}/run")
    def run(control_id: str, request: Request) -> Any:
        root = request.app.state.project_root
        executed_at = datetime.now(UTC).isoformat()
        conn = connect(root)
        try:
            rec = run_control_in_store(conn, root, control_id, executed_at)
        finally:
            conn.close()
        return RedirectResponse(
            f"/controls/{control_id}/runs/{rec.run_id}", status_code=303
        )

    @app.get("/controls/{control_id}/runs/{run_id}", response_class=HTMLResponse)
    def run_view(
        control_id: str,
        run_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        root = request.app.state.project_root
        run = repo.get_run(conn, run_id)
        wp_path = root / "target" / "workpapers" / f"{control_id}.html"
        workpaper_html = (
            wp_path.read_text(encoding="utf-8") if wp_path.exists() else ""
        )
        return templates.TemplateResponse(
            request,
            "run_view.html",
            {
                "project": repo.get_project(conn) or {"name": ""},
                "control_id": control_id,
                "run": run,
                "workpaper_html": workpaper_html,
            },
        )
