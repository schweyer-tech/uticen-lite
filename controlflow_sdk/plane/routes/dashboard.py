from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from controlflow_sdk.store import repo


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    @app.get("/", response_class=HTMLResponse)
    def dashboard(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        project = repo.get_project(conn) or {"name": ""}
        controls = repo.list_controls(conn)
        rows = []
        for c in controls:
            latest = repo.latest_run(conn, c["id"])
            rows.append({"control": c, "latest": latest})
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"project": project, "rows": rows},
        )
