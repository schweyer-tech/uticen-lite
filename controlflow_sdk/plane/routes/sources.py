from __future__ import annotations

import csv as csvmod
import io
import sqlite3
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect

PAGE_SIZE = 50

# ---- data-file versioning helpers -----------------------------------------
# Refreshing a source's data never destroys the old file: the prior file is
# copied into data/.versions/<id>/ before being overwritten, and uploads awaiting
# the user's explicit confirmation are staged under data/.pending/<id>/. Both live
# under nested dirs so the top-level data/*.csv glob (import/load_demo) ignores them.

def _pending_dir(root: Path, sid: str) -> Path:
    return root / "data" / ".pending" / sid


def _header_of(raw: bytes) -> list[str]:
    return next(csvmod.reader(io.StringIO(raw.decode("utf-8-sig"))), [])


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _row_count(raw: bytes) -> int:
    n = sum(1 for _ in csvmod.reader(io.StringIO(raw.decode("utf-8-sig"))))
    return max(0, n - 1)  # exclude header


def _reconcile_columns(
    existing: list[dict[str, Any]], new_headers: list[str]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Preserve mappings for surviving columns, default new ones, drop missing ones."""
    by_name = {c["original_name"]: c for c in existing}
    reconciled: list[dict[str, Any]] = []
    for i, h in enumerate(new_headers):
        if h in by_name:
            col = dict(by_name[h])
            col["ordinal"] = i
        else:
            col = {"original_name": h, "display_name": h, "data_type": "text",
                   "is_key": False, "include": True, "ordinal": i}
        reconciled.append(col)
    added = [h for h in new_headers if h not in by_name]
    removed = [c["original_name"] for c in existing if c["original_name"] not in new_headers]
    return reconciled, added, removed


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

    @app.get("/sources/new", response_class=HTMLResponse)
    def new_source(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        return templates.TemplateResponse(
            request,
            "source_new.html",
            {"project": repo.get_project(conn) or {"name": ""}},
        )

    @app.post("/sources")
    async def create_source(
        request: Request,
        source_id: str = Form(...),
        format: str = Form("csv"),
        as_of_date: str = Form(""),
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
            repo.set_initial_file(
                conn, source_id=source_id, stored_path=f"data/{dest.name}",
                original_name=dest.name, as_of_date=as_of_date.strip() or None,
                row_count=_row_count(raw), uploaded_at=_stamp(),
            )
            if as_of_date.strip():
                conn.execute("UPDATE sources SET extract_date = ? WHERE id = ?",
                             (as_of_date.strip(), source_id))
                conn.commit()
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
             "source": repo.get_source(conn, source_id),
             "active": "definition"},
        )

    @app.get("/sources/{source_id}/data", response_class=HTMLResponse)
    def source_data(
        source_id: str,
        request: Request,
        page: int = 1,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        root = request.app.state.project_root
        current = repo.get_current_file(conn, source_id)
        header: list[str] = []
        rows: list[list[str]] = []
        total = 0
        if current:
            fpath = root / current["stored_path"]
            if fpath.is_file():
                all_rows = list(
                    csvmod.reader(io.StringIO(fpath.read_text(encoding="utf-8-sig")))
                )
                if all_rows:
                    header, data_rows = all_rows[0], all_rows[1:]
                    total = len(data_rows)
                    page = max(1, page)
                    start = (page - 1) * PAGE_SIZE
                    rows = data_rows[start:start + PAGE_SIZE]
        page_count = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        return templates.TemplateResponse(
            request, "source_data.html",
            {"project": repo.get_project(conn) or {"name": ""},
             "source": repo.get_source(conn, source_id), "current": current,
             "header": header, "rows": rows, "total": total,
             "page": min(page, page_count), "page_count": page_count,
             "page_size": PAGE_SIZE, "active": "data"},
        )

    @app.get("/sources/{source_id}/history", response_class=HTMLResponse)
    def source_history(
        source_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Any:
        return templates.TemplateResponse(
            request, "source_history.html",
            {"project": repo.get_project(conn) or {"name": ""},
             "source": repo.get_source(conn, source_id),
             "files": repo.list_source_files(conn, source_id), "active": "history"},
        )

    @app.post("/sources/{source_id}/data/asof")
    async def update_asof(
        source_id: str,
        request: Request,
        as_of_date: str = Form(""),
    ) -> Any:
        root = request.app.state.project_root
        conn = connect(root)
        try:
            repo.set_current_file_asof(conn, source_id, as_of_date.strip() or None)
            return RedirectResponse(f"/sources/{source_id}/data", status_code=303)
        finally:
            conn.close()

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

            def _field(name: str) -> str | None:
                return str(form.get(name, "")).strip() or None

            repo.upsert_source(
                conn, id=source_id, format=existing["format"],
                path=existing["path"], key_config=key_config,
                title=_field("title"),
                description=_field("description"),
                # Not exposed in the editor form — preserve the imported value.
                completeness_accuracy=existing.get("completeness_accuracy"),
                extract_date=existing.get("extract_date"),
            )
        finally:
            conn.close()
        return RedirectResponse("/sources", status_code=303)

    @app.post("/sources/{source_id}/refresh")
    async def refresh_source(
        source_id: str,
        request: Request,
        file: UploadFile = File(...),
        as_of_date: str = Form(""),
    ) -> Any:
        """Stage a new data file and show a confirm page with the column diff."""
        root = request.app.state.project_root
        conn = connect(root)
        try:
            existing = repo.get_source(conn, source_id)
            if existing is None:
                return RedirectResponse("/sources", status_code=303)
            raw = await file.read()
            pdir = _pending_dir(root, source_id)
            pdir.mkdir(parents=True, exist_ok=True)
            pending_name = Path(file.filename or f"{source_id}.csv").name
            (pdir / pending_name).write_bytes(raw)
            new_headers = _header_of(raw)
            _, added, removed = _reconcile_columns(existing["columns"], new_headers)
            return templates.TemplateResponse(
                request,
                "source_refresh.html",
                {"project": repo.get_project(conn) or {"name": ""},
                 "source": existing, "pending": pending_name,
                 "new_headers": new_headers, "added": added, "removed": removed,
                 "as_of_date": as_of_date},
            )
        finally:
            conn.close()

    @app.post("/sources/{source_id}/refresh/confirm")
    async def confirm_refresh(
        source_id: str,
        request: Request,
        pending: str = Form(...),
        as_of_date: str = Form(""),
    ) -> Any:
        """Archive the current file, promote the staged file, reconcile columns."""
        root = request.app.state.project_root
        conn = connect(root)
        try:
            existing = repo.get_source(conn, source_id)
            if existing is None:
                return RedirectResponse("/sources", status_code=303)
            pending_path = _pending_dir(root, source_id) / Path(pending).name
            if not pending_path.is_file():
                return RedirectResponse(f"/sources/{source_id}", status_code=303)
            new_bytes = pending_path.read_bytes()

            current_path = root / existing["path"]
            stamp = _stamp()
            archive_rel = Path("data/.versions") / source_id / f"{stamp}__{current_path.name}"
            if current_path.is_file():  # never overwrite without keeping the old file
                adir = root / "data" / ".versions" / source_id
                adir.mkdir(parents=True, exist_ok=True)
                (root / archive_rel).write_bytes(current_path.read_bytes())
                repo.archive_current_file(conn, source_id, str(archive_rel))
            current_path.parent.mkdir(parents=True, exist_ok=True)
            current_path.write_bytes(new_bytes)  # path stays stable across refreshes
            pending_path.unlink()

            reconciled, _, removed = _reconcile_columns(
                existing["columns"], _header_of(new_bytes)
            )
            repo.set_columns(conn, source_id, reconciled)

            # Drop any dropped column from the key config so it can't dangle.
            key_cols = [c for c in (existing.get("key_config") or {}).get("columns", [])
                        if c not in removed]
            if len(key_cols) == 1:
                key_config: dict[str, Any] = {"mode": "single", "columns": key_cols}
            elif key_cols:
                key_config = {"mode": "composite", "columns": key_cols}
            else:
                key_config = {"mode": "auto"}

            # Record the new current file version (old one archived above if it existed).
            repo.record_current_file(
                conn, source_id=source_id, stored_path=existing["path"],
                original_name=Path(pending).name,
                as_of_date=as_of_date.strip() or None,
                row_count=_row_count(new_bytes), uploaded_at=stamp,
            )

            new_extract_date = as_of_date.strip() or existing.get("extract_date")
            repo.upsert_source(
                conn, id=source_id, format=existing["format"],
                path=existing["path"], key_config=key_config,
                title=existing.get("title"), description=existing.get("description"),
                completeness_accuracy=existing.get("completeness_accuracy"),
                extract_date=new_extract_date,
            )
            return RedirectResponse(f"/sources/{source_id}", status_code=303)
        finally:
            conn.close()

    @app.post("/sources/{source_id}/refresh/cancel")
    async def cancel_refresh(
        source_id: str,
        request: Request,
        pending: str = Form(""),
    ) -> Any:
        """Discard a staged file without touching the current data."""
        root = request.app.state.project_root
        if pending:
            p = _pending_dir(root, source_id) / Path(pending).name
            if p.is_file():
                p.unlink()
        return RedirectResponse(f"/sources/{source_id}", status_code=303)
