from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from controlflow_sdk.store.db import connect
from controlflow_sdk.store.migrations import migrate

_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))


def get_conn(request: Request) -> Generator[sqlite3.Connection, None, None]:
    root: Path = request.app.state.project_root
    conn = connect(root)
    try:
        yield conn
    finally:
        conn.close()


def create_app(project_root: Path) -> FastAPI:
    project_root = Path(project_root)
    (project_root / "data").mkdir(parents=True, exist_ok=True)
    conn = connect(project_root)
    migrate(conn)
    conn.close()

    app = FastAPI(title="ControlFlow Control Plane")
    app.state.project_root = project_root
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    from controlflow_sdk.plane.routes import (
        ai,
        controls,
        dashboard,
        export,
        runs,
        setup,
        sources,
    )

    dashboard.register(app, templates, get_conn)
    setup.register(app, templates, get_conn)
    sources.register(app, templates, get_conn)
    controls.register(app, templates, get_conn)
    ai.register(app, templates, get_conn)
    runs.register(app, templates, get_conn)
    export.register(app, templates, get_conn)
    return app
