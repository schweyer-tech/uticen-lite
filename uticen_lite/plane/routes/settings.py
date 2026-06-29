"""General settings: the Settings landing section + engagement rename.

The nav "Settings" link lands here (``GET /settings``) — a section that fans out
to the engagement details (rename) and AI-assisted authoring (``/settings/ai``),
so "Settings" is no longer AI-only.

Single-engagement by design (STRATEGY.md non-goal: not a platform / no
multi-tenant). The only mutation here is renaming the *current* engagement — the
project name shown top-right. The rename is just an update to the free-text
``project.name`` string; it does not change the bundle shape (the schema's
``project.name`` is any string), so it stays bundle-compatible (learning 0001).

The read-only ``GET`` uses ``Depends(get_conn)``; the writing ``POST`` opens its
own per-handler connection (learning 0002).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.import_service import reset_to_demo


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    @app.get("/settings", response_class=HTMLResponse)
    def settings_home(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "settings.html",
            {"project": repo.get_project(conn) or {"name": ""}},
        )

    @app.post("/settings/rename")
    async def rename_engagement(request: Request) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name") or "").strip()
        # Blank rename is a no-op — never wipe the engagement name.
        if not name:
            return RedirectResponse(url="/settings", status_code=303)

        conn = connect(request.app.state.project_root)  # per-handler conn (0002)
        try:
            project = repo.get_project(conn) or {}
            # Preserve framework, system (incl. the AI selection), and created_at;
            # only the human-readable name changes.
            repo.upsert_project(
                conn,
                name=name,
                framework=project.get("framework"),
                system=project.get("system") or {},
                created_at=project.get("created_at", "") or "",
            )
            return RedirectResponse(url="/settings", status_code=303)
        finally:
            conn.close()

    @app.post("/settings/reset-demo")
    async def reset_demo(request: Request) -> RedirectResponse:
        # Destructive recovery: wipe the engagement and reload the Northwind demo.
        # Per-handler connection (learning 0002).
        root = request.app.state.project_root
        conn = connect(root)
        try:
            reset_to_demo(conn, root)
            return RedirectResponse(url="/", status_code=303)
        finally:
            conn.close()
