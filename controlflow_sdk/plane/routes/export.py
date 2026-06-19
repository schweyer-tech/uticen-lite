from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.export_service import build_bundle


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    @app.get("/export", response_class=HTMLResponse)
    def export_page(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        project = repo.get_project(conn) or {"name": ""}
        return templates.TemplateResponse(request, "export.html", {"project": project})

    @app.post("/export", response_model=None)
    def export_bundle(request: Request) -> Response:
        root = Path(str(request.app.state.project_root))
        out = root / "target" / "bundle.zip"
        generated_at = datetime.now(UTC).isoformat()
        conn = connect(root)
        try:
            build_bundle(conn, root, out, generated_at)
        except ValueError as exc:
            # No runs in the store yet — return a friendly 400 instead of 500.
            return JSONResponse(
                status_code=400,
                content={"error": str(exc), "detail": "Run a control first, then export."},
            )
        finally:
            conn.close()
        return FileResponse(str(out), media_type="application/zip", filename="bundle.zip")
