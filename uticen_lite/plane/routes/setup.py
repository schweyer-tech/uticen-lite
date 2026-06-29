"""First-run onboarding: name the engagement or load the Northwind demo.

The dashboard renders ``setup.html`` when the engagement has no name yet; these
POST handlers act on that screen. Both write to the store, so each opens its own
connection in the handler body (see docs/learnings/0002).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.import_service import load_demo


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    @app.post("/setup")
    async def setup_name(request: Request) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name") or "").strip()
        framework = str(form.get("framework") or "").strip() or None
        if not name:
            return RedirectResponse(url="/", status_code=303)

        conn = connect(request.app.state.project_root)
        try:
            repo.upsert_project(
                conn,
                name=name,
                framework=framework,
                created_at=datetime.now(UTC).isoformat(),
            )
            return RedirectResponse(url="/", status_code=303)
        finally:
            conn.close()

    @app.post("/setup/demo")
    async def setup_demo(request: Request) -> RedirectResponse:
        root = request.app.state.project_root
        conn = connect(root)
        try:
            load_demo(conn, root)
            return RedirectResponse(url="/", status_code=303)
        finally:
            conn.close()
