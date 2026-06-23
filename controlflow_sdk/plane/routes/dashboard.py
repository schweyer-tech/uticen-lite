from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from controlflow_sdk.store import repo
from controlflow_sdk.upgrade.spawn import read_status


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    @app.get("/", response_class=HTMLResponse)
    def dashboard(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        project = repo.get_project(conn) or {"name": ""}
        # First run: no engagement name yet → show the onboarding screen instead of
        # an empty dashboard (issue #11).
        if not project.get("name"):
            return templates.TemplateResponse(request, "setup.html", {"project": project})
        controls = repo.list_controls(conn)
        rows = []
        for c in controls:
            latest = repo.latest_run(conn, c["id"])
            rows.append({"control": c, "latest": latest})
        notice = read_status(request.app.state.project_root)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"project": project, "rows": rows, "upgrade_notice": notice},
        )
