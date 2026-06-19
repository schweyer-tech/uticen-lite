from __future__ import annotations

import csv as csvmod
import io
import sqlite3
from collections.abc import Callable, Generator
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    @app.get("/sources", response_class=HTMLResponse)
    def list_sources(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        return templates.TemplateResponse(
            request,
            "sources.html",
            {"project": repo.get_project(conn) or {"name": ""}, "sources": repo.list_sources(conn)},
        )

    @app.post("/sources")
    async def create_source(
        request: Request,
        source_id: str = Form(...),
        format: str = Form("csv"),
        file: UploadFile = File(...),
    ) -> Any:
        root = request.app.state.project_root
        conn = connect(root)
        try:
            (root / "data").mkdir(parents=True, exist_ok=True)
            raw = await file.read()
            dest = root / "data" / (file.filename or f"{source_id}.csv")
            dest.write_bytes(raw)
            header = next(csvmod.reader(io.StringIO(raw.decode("utf-8-sig"))), [])
            repo.upsert_source(conn, id=source_id, format=format,
                               path=f"data/{dest.name}", key_config={"mode": "auto"})
            repo.set_columns(conn, source_id, [
                {"original_name": h, "display_name": h, "data_type": "text",
                 "is_key": False, "include": True, "ordinal": i}
                for i, h in enumerate(header)
            ])
        finally:
            conn.close()
        return RedirectResponse(f"/sources/{source_id}", status_code=303)

    @app.get("/sources/{source_id}", response_class=HTMLResponse)
    def edit_source(
        source_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        return templates.TemplateResponse(
            request,
            "source_edit.html",
            {"project": repo.get_project(conn) or {"name": ""},
             "source": repo.get_source(conn, source_id)},
        )

    @app.post("/sources/{source_id}")
    async def save_source(
        source_id: str,
        request: Request,
    ) -> Any:
        root = request.app.state.project_root
        conn = connect(root)
        try:
            form = await request.form()
            existing = repo.get_source(conn, source_id)
            if existing is None:
                return RedirectResponse("/sources", status_code=303)
            key_columns = [
                k.strip()
                for k in str(form.get("key_columns", "")).split(",")
                if k.strip()
            ]
            columns = []
            for i, col in enumerate(existing["columns"]):
                name = col["original_name"]
                columns.append({
                    "original_name": name,
                    "display_name": form.get(f"display_name__{name}", name),
                    "data_type": form.get(f"data_type__{name}", "text"),
                    "is_key": name in key_columns,
                    "include": form.get(f"include__{name}") is not None,
                    "ordinal": i,
                })
            repo.set_columns(conn, source_id, columns)
            if len(key_columns) == 1:
                key_config: dict[str, Any] = {"mode": "single", "columns": key_columns}
            elif key_columns:
                key_config = {"mode": "composite", "columns": key_columns}
            else:
                key_config = {"mode": "auto"}
            repo.upsert_source(conn, id=source_id, format=existing["format"],
                               path=existing["path"], key_config=key_config)
        finally:
            conn.close()
        return RedirectResponse("/sources", status_code=303)
