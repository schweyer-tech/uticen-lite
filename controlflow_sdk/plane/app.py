from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from controlflow_sdk.store.db import connect
from controlflow_sdk.store.migrations import migrate

_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))

# A tiny inline favicon (accent-blue rounded square + check glyph) so the
# browser's automatic /favicon.ico request gets a 200 instead of logging a 404
# on every page. Served inline to avoid shipping a binary asset.
_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#2f6df6"/>'
    '<path d="M9 16.5l4.5 4.5L23 11" fill="none" stroke="#fff" '
    'stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


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

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(content=_FAVICON_SVG, media_type="image/svg+xml")

    from controlflow_sdk.plane.routes import (
        ai,
        controls,
        dashboard,
        export,
        pipeline,
        runs,
        settings,
        setup,
        sources,
    )

    dashboard.register(app, templates, get_conn)
    setup.register(app, templates, get_conn)
    # Register the general Settings landing (/settings, /settings/rename) BEFORE
    # ai.register() so the AI sub-routes (/settings/ai) sit alongside it; both
    # share the /settings prefix and neither shadows the other (learning 0007).
    settings.register(app, templates, get_conn)
    sources.register(app, templates, get_conn)
    # Register the pipeline sub-routes (/controls/{id}/pipeline*) BEFORE the
    # /controls/{control_id} catch-all in controls.register() so they cannot be
    # shadowed (learning 0007).
    pipeline.register(app, templates, get_conn)
    controls.register(app, templates, get_conn)
    ai.register(app, templates, get_conn)
    runs.register(app, templates, get_conn)
    export.register(app, templates, get_conn)
    return app
