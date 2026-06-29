from __future__ import annotations

import json as jsonmod
import sqlite3
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from uticen_lite.plane import fetch as fetchmod
from uticen_lite.plane.coercion_check import coercion_report
from uticen_lite.plane.ingest import (
    AdaptersUnavailable,
    ExtractedTable,
    TableParseError,
    extract_table,
)
from uticen_lite.store import repo
from uticen_lite.store.db import connect

_UPLOAD_FORMATS = {".csv": "csv", ".xlsx": "xlsx", ".parquet": "parquet"}


def _fmt_from_name(name: str) -> str | None:
    return _UPLOAD_FORMATS.get(Path(name).suffix.lower())

PAGE_SIZE = 50

# ---- data-file versioning helpers -----------------------------------------
# Refreshing a source's data never destroys the old file: the prior file is
# copied into data/.versions/<id>/ before being overwritten, and uploads awaiting
# the user's explicit confirmation are staged under data/.pending/<id>/. Both live
# under nested dirs so the top-level data/*.csv glob (import/load_demo) ignores them.

def _pending_dir(root: Path, sid: str) -> Path:
    return root / "data" / ".pending" / sid


def _table_of(raw: bytes, fmt: str, sheet: str | None = None) -> ExtractedTable:
    return extract_table(raw, fmt, sheet=sheet)


def _header_of(raw: bytes, fmt: str = "csv", sheet: str | None = None) -> list[str]:
    return _table_of(raw, fmt, sheet).header


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _fmt_stamp(stamp: str) -> str:
    """Render an internal upload stamp (20260620T101913Z) for humans."""
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return stamp


def _row_count(raw: bytes, fmt: str = "csv", sheet: str | None = None) -> int:
    return len(_table_of(raw, fmt, sheet).rows)


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


def _coerce_date(val: str) -> str | None:
    """Normalise a posted as-of date: trimmed string, or None when blank."""
    return val.strip() or None


def _set_extract_date(conn: sqlite3.Connection, source_id: str, as_of_date: str) -> None:
    """Stamp the source's extract_date from a posted as-of date (no-op when blank)."""
    if (stripped := as_of_date.strip()):
        conn.execute("UPDATE sources SET extract_date = ? WHERE id = ?", (stripped, source_id))
        conn.commit()


def register(
    app: FastAPI,
    templates: Jinja2Templates,
    get_conn: Callable[..., Generator[sqlite3.Connection, None, None]],
) -> None:
    def _project(root: object) -> dict:
        """Read the engagement so EVERY add-source response keeps the global nav +
        chip. Hardcoding ``{"name": ""}`` dropped the whole header on the URL tab and
        on form errors (audit B1). Opens its own connection (learning 0002)."""
        conn = connect(root)  # type: ignore[arg-type]
        try:
            return repo.get_project(conn) or {"name": ""}
        finally:
            conn.close()

    @app.get("/sources", response_class=HTMLResponse)
    def list_sources(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "sources.html",
            {"project": repo.get_project(conn) or {"name": ""}, "sources": repo.list_sources(conn)},
        )

    @app.get("/sources/new", response_class=HTMLResponse)
    def new_source(
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "source_new.html",
            {"project": repo.get_project(conn) or {"name": ""}},
        )

    @app.post("/sources", response_model=None)
    async def create_source(
        request: Request,
        source_id: str = Form(...),
        as_of_date: str = Form(""),
        sheet: str = Form(""),
        file: UploadFile = File(...),
    ) -> HTMLResponse | RedirectResponse:
        root = request.app.state.project_root
        filename = file.filename or f"{source_id}.csv"
        fmt = _fmt_from_name(filename)
        raw = await file.read()

        def _err(msg: str) -> HTMLResponse:
            return templates.TemplateResponse(
                request, "source_new.html",
                {"project": _project(root), "error": msg}, status_code=200,
            )

        if fmt is None:
            return _err(
                f"Unsupported file type for {filename!r}. "
                "Upload a .csv, .xlsx, or .parquet file (legacy .xls is not supported)."
            )
        sheet_val = sheet.strip() or None
        try:
            table = extract_table(raw, fmt, sheet=sheet_val)
        except (AdaptersUnavailable, TableParseError) as e:
            return _err(str(e))

        conn = connect(root)
        try:
            (root / "data").mkdir(parents=True, exist_ok=True)
            dest = root / "data" / Path(filename).name
            dest.write_bytes(raw)
            repo.upsert_source(conn, id=source_id, format=fmt,
                               path=f"data/{dest.name}", key_config={"mode": "auto"},
                               sheet=sheet_val)
            repo.set_columns(conn, source_id, [
                {"original_name": h, "display_name": h, "data_type": "text",
                 "is_key": False, "include": True, "ordinal": i}
                for i, h in enumerate(table.header)
            ])
            repo.set_initial_file(
                conn, source_id=source_id, stored_path=f"data/{dest.name}",
                original_name=dest.name, as_of_date=_coerce_date(as_of_date),
                row_count=len(table.rows), uploaded_at=_stamp(),
            )
            _set_extract_date(conn, source_id, as_of_date)
        finally:
            conn.close()
        return RedirectResponse(f"/sources/{source_id}", status_code=303)

    def _do_fetch(request: Request, url: str, headers: dict, record_path: str | None):
        # Tests inject app.state.fetch_opener; production uses the default opener.
        opener = getattr(request.app.state, "fetch_opener", None)
        return fetchmod.fetch_snapshot(url, headers=headers or None,
                                       record_path=record_path or None, opener=opener)

    def _parse_headers(raw: str) -> dict:
        raw = (raw or "").strip()
        if not raw:
            return {}
        try:
            parsed = jsonmod.loads(raw)
        except jsonmod.JSONDecodeError as e:
            raise fetchmod.FetchError(f"Headers must be a JSON object: {e}") from e
        if not isinstance(parsed, dict):
            raise fetchmod.FetchError("Headers must be a JSON object, e.g. "
                                      '{"Authorization": "Bearer ..."}')
        return {str(k): str(v) for k, v in parsed.items()}

    @app.get("/sources/from-url", response_class=HTMLResponse)
    def new_source_from_url(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "source_new.html",
            {"project": _project(request.app.state.project_root), "mode": "url"},
        )

    @app.post("/sources/from-url", response_model=None)
    async def create_source_from_url(
        request: Request,
        source_id: str = Form(...),
        url: str = Form(...),
        headers: str = Form(""),
        record_path: str = Form(""),
        as_of_date: str = Form(""),
    ) -> HTMLResponse | RedirectResponse:
        root = request.app.state.project_root

        def _err(msg: str) -> HTMLResponse:
            return templates.TemplateResponse(
                request, "source_new.html",
                {"project": _project(root), "mode": "url", "error": msg,
                 "url": url, "record_path": record_path}, status_code=200,
            )

        try:
            hdrs = _parse_headers(headers)
            snap = _do_fetch(request, url, hdrs, record_path.strip())
            table = extract_table(snap.raw, snap.fmt)
        except (fetchmod.FetchError, AdaptersUnavailable, TableParseError) as e:
            return _err(str(e))

        conn = connect(root)
        try:
            (root / "data").mkdir(parents=True, exist_ok=True)
            dest = root / "data" / snap.suggested_name
            dest.write_bytes(snap.raw)
            repo.upsert_source(conn, id=source_id, format=snap.fmt,
                               path=f"data/{dest.name}", key_config={"mode": "auto"})
            repo.set_columns(conn, source_id, [
                {"original_name": h, "display_name": h, "data_type": "text",
                 "is_key": False, "include": True, "ordinal": i}
                for i, h in enumerate(table.header)
            ])
            repo.set_initial_file(
                conn, source_id=source_id, stored_path=f"data/{dest.name}",
                original_name=dest.name, as_of_date=_coerce_date(as_of_date),
                row_count=len(table.rows), uploaded_at=_stamp(),
            )
            repo.upsert_source_fetch(conn, source_id=source_id, url=snap.source_url,
                                     headers=hdrs, record_path=record_path.strip() or None,
                                     last_fetched_at=snap.fetched_at)
            _set_extract_date(conn, source_id, as_of_date)
        finally:
            conn.close()
        return RedirectResponse(f"/sources/{source_id}", status_code=303)

    @app.get("/sources/{source_id}", response_class=HTMLResponse)
    def edit_source(
        source_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
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
    ) -> HTMLResponse:
        root = request.app.state.project_root
        source = repo.get_source(conn, source_id)
        current = repo.get_current_file(conn, source_id)
        header: list[str] = []
        rows: list[list[str]] = []
        data_rows: list[list[str]] = []
        total = 0
        adapters_error: str | None = None
        if current:
            fpath = root / current["stored_path"]
            if fpath.is_file():
                fmt = (source or {}).get("format", "csv")
                sheet = (source or {}).get("sheet")
                # Never 500: an xlsx/parquet source viewed without the [adapters]
                # extra degrades to a friendly banner instead of raising.
                try:
                    table = extract_table(fpath.read_bytes(), fmt, sheet=sheet)
                    header, data_rows = table.header, table.rows
                    total = len(data_rows)
                    page = max(1, page)
                    start = (page - 1) * PAGE_SIZE
                    rows = data_rows[start:start + PAGE_SIZE]
                except (AdaptersUnavailable, TableParseError) as e:
                    adapters_error = str(e)
        page_count = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        # Coercion-health verdict computed over the FULL file (not the paginated
        # slice) so it stays honest even when page 1 happens to be clean (0004).
        coercion: list[dict] = []
        if header and source:
            coercion = coercion_report(header, data_rows, source["columns"])
        return templates.TemplateResponse(
            request, "source_data.html",
            {"project": repo.get_project(conn) or {"name": ""},
             "source": source, "current": current,
             "header": header, "rows": rows, "total": total,
             "page": min(page, page_count), "page_count": page_count,
             "page_size": PAGE_SIZE, "coercion": coercion,
             "adapters_error": adapters_error, "active": "data",
             "fetch": repo.get_source_fetch(conn, source_id)},
        )

    @app.get("/sources/{source_id}/history", response_class=HTMLResponse)
    def source_history(
        source_id: str,
        request: Request,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        files = repo.list_source_files(conn, source_id)
        for f in files:
            f["uploaded"] = _fmt_stamp(f["uploaded_at"]) if f["uploaded_at"] else ""
        return templates.TemplateResponse(
            request, "source_history.html",
            {"project": repo.get_project(conn) or {"name": ""},
             "source": repo.get_source(conn, source_id),
             "files": files, "active": "history",
             "fetch": repo.get_source_fetch(conn, source_id)},
        )

    @app.post("/sources/{source_id}/data/asof")
    async def update_asof(
        source_id: str,
        request: Request,
        as_of_date: str = Form(""),
    ) -> RedirectResponse:
        root = request.app.state.project_root
        conn = connect(root)
        try:
            repo.set_current_file_asof(conn, source_id, _coerce_date(as_of_date))
            return RedirectResponse(f"/sources/{source_id}/data", status_code=303)
        finally:
            conn.close()

    @app.post("/sources/{source_id}")
    async def save_source(
        source_id: str,
        request: Request,
    ) -> RedirectResponse:
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
                sheet=existing.get("sheet"),
            )
        finally:
            conn.close()
        return RedirectResponse("/sources", status_code=303)

    @app.post("/sources/{source_id}/refresh", response_model=None)
    async def refresh_source(
        source_id: str,
        request: Request,
        file: UploadFile = File(...),
        as_of_date: str = Form(""),
    ) -> HTMLResponse | RedirectResponse:
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
            try:
                new_headers = _header_of(raw, existing["format"], existing.get("sheet"))
            except (AdaptersUnavailable, TableParseError) as e:
                (pdir / pending_name).unlink(missing_ok=True)
                return templates.TemplateResponse(
                    request, "source_edit.html",
                    {"project": repo.get_project(conn) or {"name": ""},
                     "source": existing, "active": "definition", "error": str(e)},
                    status_code=200,
                )
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
    ) -> RedirectResponse:
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
                existing["columns"],
                _header_of(new_bytes, existing["format"], existing.get("sheet")),
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
                as_of_date=_coerce_date(as_of_date),
                row_count=_row_count(new_bytes, existing["format"], existing.get("sheet")),
                uploaded_at=stamp,
            )

            new_extract_date = as_of_date.strip() or existing.get("extract_date")
            repo.upsert_source(
                conn, id=source_id, format=existing["format"],
                path=existing["path"], key_config=key_config,
                title=existing.get("title"), description=existing.get("description"),
                completeness_accuracy=existing.get("completeness_accuracy"),
                extract_date=new_extract_date,
                sheet=existing.get("sheet"),
            )
            return RedirectResponse(f"/sources/{source_id}", status_code=303)
        finally:
            conn.close()

    @app.post("/sources/{source_id}/refresh/cancel")
    async def cancel_refresh(
        source_id: str,
        request: Request,
        pending: str = Form(""),
    ) -> RedirectResponse:
        """Discard a staged file without touching the current data."""
        root = request.app.state.project_root
        if pending:
            p = _pending_dir(root, source_id) / Path(pending).name
            if p.is_file():
                p.unlink()
        return RedirectResponse(f"/sources/{source_id}", status_code=303)

    @app.post("/sources/{source_id}/refetch", response_model=None)
    async def refetch_source(source_id: str, request: Request) -> HTMLResponse | RedirectResponse:
        """Re-run the stored URL fetch and route through the refresh-confirm diff."""
        root = request.app.state.project_root
        conn = connect(root)
        try:
            existing = repo.get_source(conn, source_id)
            fetch_row = repo.get_source_fetch(conn, source_id)
            if existing is None or fetch_row is None:
                return RedirectResponse(f"/sources/{source_id}", status_code=303)
            try:
                snap = _do_fetch(request, fetch_row["url"], fetch_row["headers"],
                                 fetch_row.get("record_path"))
                new_headers = extract_table(snap.raw, snap.fmt).header
            except (fetchmod.FetchError, AdaptersUnavailable, TableParseError) as e:
                return templates.TemplateResponse(
                    request, "source_data.html",
                    {"project": repo.get_project(conn) or {"name": ""},
                     "source": existing, "current": repo.get_current_file(conn, source_id),
                     "header": [], "rows": [], "total": 0, "page": 1, "page_count": 1,
                     "page_size": PAGE_SIZE, "coercion": [], "active": "data",
                     "fetch": fetch_row, "error": str(e), "adapters_error": None}, status_code=200,
                )
            pdir = _pending_dir(root, source_id)
            pdir.mkdir(parents=True, exist_ok=True)
            (pdir / snap.suggested_name).write_bytes(snap.raw)
            _, added, removed = _reconcile_columns(existing["columns"], new_headers)
            # Refresh last_fetched_at provenance now (the snapshot was taken).
            repo.upsert_source_fetch(conn, source_id=source_id, url=fetch_row["url"],
                                     headers=fetch_row["headers"],
                                     record_path=fetch_row.get("record_path"),
                                     last_fetched_at=snap.fetched_at)
            return templates.TemplateResponse(
                request, "source_refresh.html",
                {"project": repo.get_project(conn) or {"name": ""},
                 "source": existing, "pending": snap.suggested_name,
                 "new_headers": new_headers, "added": added, "removed": removed,
                 "as_of_date": ""},
            )
        finally:
            conn.close()
