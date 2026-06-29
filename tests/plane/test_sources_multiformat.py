from __future__ import annotations

import io

import pandas as pd


def _xlsx(df_by_sheet: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in df_by_sheet.items():
            df.to_excel(xw, sheet_name=name, index=False)
    return buf.getvalue()


def test_upload_xlsx_infers_columns_and_format(client):
    raw = _xlsx({"Sheet1": pd.DataFrame({"user_id": ["U1"], "amount": [5]})})
    resp = client.post("/sources",
                       data={"source_id": "gl", "as_of_date": "2026-01-01"},
                       files={"file": ("gl.xlsx", io.BytesIO(raw),
                              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                       follow_redirects=False)
    assert resp.status_code in (302, 303)
    edit = client.get("/sources/gl")
    assert "user_id" in edit.text and "amount" in edit.text
    from uticen_lite.store import repo
    from uticen_lite.store.db import connect
    conn = connect(client.app.state.project_root)
    assert repo.get_source(conn, "gl")["format"] == "xlsx"
    conn.close()


def test_upload_parquet(client):
    buf = io.BytesIO()
    pd.DataFrame({"id": ["A", "B"], "n": [1, 2]}).to_parquet(buf, index=False)
    resp = client.post("/sources",
                       data={"source_id": "p", "as_of_date": "2026-01-01"},
                       files={"file": ("p.parquet", io.BytesIO(buf.getvalue()),
                              "application/octet-stream")},
                       follow_redirects=False)
    assert resp.status_code in (302, 303)
    data = client.get("/sources/p/data")
    assert "A" in data.text and "B" in data.text  # preview renders parquet rows


def test_xlsx_sheet_selection_persisted(client):
    raw = _xlsx({"First": pd.DataFrame({"id": ["A"]}),
                 "Second": pd.DataFrame({"id": ["Z"]})})
    client.post("/sources",
                data={"source_id": "ms", "as_of_date": "2026-01-01", "sheet": "Second"},
                files={"file": ("ms.xlsx", io.BytesIO(raw),
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                follow_redirects=False)
    from uticen_lite.store import repo
    from uticen_lite.store.db import connect
    conn = connect(client.app.state.project_root)
    assert repo.get_source(conn, "ms")["sheet"] == "Second"
    conn.close()


def test_data_preview_degrades_when_adapters_unavailable(client, monkeypatch):
    """GET /sources/{id}/data must not 500 if [adapters] is gone post-create."""
    raw = _xlsx({"Sheet1": pd.DataFrame({"user_id": ["U1"], "amount": [5]})})
    client.post("/sources",
                data={"source_id": "gone", "as_of_date": "2026-01-01"},
                files={"file": ("gone.xlsx", io.BytesIO(raw),
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                follow_redirects=False)
    # Now simulate the [adapters] extra being absent for the preview read.
    from uticen_lite.plane.ingest import AdaptersUnavailable
    msg = ("Excel/Parquet support needs the optional dependencies: "
           "pip install 'uticen-lite[adapters]'")

    def _raise(*a, **k):
        raise AdaptersUnavailable(msg)

    monkeypatch.setattr("uticen_lite.plane.routes.sources.extract_table", _raise)
    resp = client.get("/sources/gone/data")
    assert resp.status_code == 200  # friendly degrade, never a 500
    assert "uticen-lite[adapters]" in resp.text


def test_unsupported_xls_rejected(client):
    resp = client.post("/sources",
                       data={"source_id": "old", "as_of_date": "2026-01-01"},
                       files={"file": ("old.xls", io.BytesIO(b"x"), "application/vnd.ms-excel")},
                       follow_redirects=False)
    assert resp.status_code == 200  # re-renders the form, not a redirect
    assert ".xls" in resp.text or "not supported" in resp.text.lower()


# ---------------------------------------------------------------------------
# Fix 1: confirm_refresh must preserve the sheet field
# ---------------------------------------------------------------------------

def test_confirm_refresh_preserves_sheet(client, tmp_path):
    """Sheet must survive refresh-confirm (was being silently set to NULL)."""
    raw = _xlsx({"First": pd.DataFrame({"id": ["A"]}),
                 "Second": pd.DataFrame({"id": ["Z"]})})
    # Create source with sheet="Second"
    client.post("/sources",
                data={"source_id": "refresh_sheet", "as_of_date": "2026-01-01",
                      "sheet": "Second"},
                files={"file": ("rs.xlsx", io.BytesIO(raw),
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                follow_redirects=False)

    # Stage a refresh (same file is fine)
    client.post("/sources/refresh_sheet/refresh",
                data={"as_of_date": "2026-06-01"},
                files={"file": ("rs.xlsx", io.BytesIO(raw),
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})

    # Confirm the refresh
    client.post("/sources/refresh_sheet/refresh/confirm",
                data={"pending": "rs.xlsx", "as_of_date": "2026-06-01"})

    from uticen_lite.store import repo
    from uticen_lite.store.db import connect
    conn = connect(client.app.state.project_root)
    source = repo.get_source(conn, "refresh_sheet")
    conn.close()
    assert source["sheet"] == "Second", (
        f"Expected sheet='Second', got {source['sheet']!r} — confirm_refresh nulled the sheet"
    )


def test_save_source_preserves_sheet(client):
    """Saving column metadata (POST /sources/{id}) must not wipe the sheet field."""
    raw = _xlsx({"Main": pd.DataFrame({"val": [1]}),
                 "Alt": pd.DataFrame({"val": [2]})})
    client.post("/sources",
                data={"source_id": "ss_sheet", "as_of_date": "2026-01-01", "sheet": "Alt"},
                files={"file": ("ss.xlsx", io.BytesIO(raw),
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                follow_redirects=False)

    # Submit the column-metadata form without changing anything structural
    client.post("/sources/ss_sheet",
                data={"display_name__val": "val", "data_type__val": "text",
                      "include__val": "on"},
                follow_redirects=False)

    from uticen_lite.store import repo
    from uticen_lite.store.db import connect
    conn = connect(client.app.state.project_root)
    source = repo.get_source(conn, "ss_sheet")
    conn.close()
    assert source["sheet"] == "Alt", (
        f"Expected sheet='Alt', got {source['sheet']!r} — save_source nulled the sheet"
    )


# ---------------------------------------------------------------------------
# Fix 2: corrupt file uploads must degrade to friendly errors, never 500
# ---------------------------------------------------------------------------

def test_corrupt_xlsx_upload_is_friendly(client):
    """POST /sources with corrupt xlsx bytes must return 200 with a friendly message."""
    resp = client.post("/sources",
                       data={"source_id": "bad_xlsx", "as_of_date": "2026-01-01"},
                       files={"file": ("bad.xlsx", io.BytesIO(b"not really xlsx"),
                              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                       follow_redirects=False)
    assert resp.status_code == 200, f"Expected 200 friendly page, got {resp.status_code}"
    body = resp.text.lower()
    # Must contain a human-readable error about the file being bad
    friendly_kws = ("corrupt", "could not be read", "not a valid", "bad", "error")
    assert any(kw in body for kw in friendly_kws), "Expected a friendly error message in body"


def test_corrupt_parquet_upload_is_friendly(client):
    """POST /sources with corrupt parquet bytes must return 200 with a friendly message."""
    resp = client.post("/sources",
                       data={"source_id": "bad_pq", "as_of_date": "2026-01-01"},
                       files={"file": ("bad.parquet", io.BytesIO(b"garbage parquet"),
                              "application/octet-stream")},
                       follow_redirects=False)
    assert resp.status_code == 200, f"Expected 200 friendly page, got {resp.status_code}"
    body = resp.text.lower()
    friendly_kws = ("corrupt", "could not be read", "not a valid", "bad", "error")
    assert any(kw in body for kw in friendly_kws), "Expected a friendly error message in body"


# ---------------------------------------------------------------------------
# Fix 3: History tab must show the plaintext-credentials warning
# ---------------------------------------------------------------------------

def test_history_tab_shows_secrets_warning_for_url_source(client):
    """GET /sources/{id}/history must show the plaintext-credentials warning for URL sources."""
    from uticen_lite.store import repo
    from uticen_lite.store.db import connect

    # Create a minimal file-based source first
    raw = b"id,name\n1,foo\n"
    client.post("/sources",
                data={"source_id": "url_src", "as_of_date": "2026-01-01"},
                files={"file": ("url_src.csv", io.BytesIO(raw), "text/csv")},
                follow_redirects=False)

    # Inject a source_fetch row to simulate a URL-backed source
    conn = connect(client.app.state.project_root)
    repo.upsert_source_fetch(conn, source_id="url_src",
                             url="https://example.com/data.csv",
                             headers={}, record_path=None, last_fetched_at="2026-01-01T00:00:00Z")
    conn.close()

    resp = client.get("/sources/url_src/history")
    assert resp.status_code == 200
    assert "plaintext" in resp.text, (
        "Expected the plaintext-credentials warning in the History tab"
    )
