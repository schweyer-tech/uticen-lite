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
    from controlflow_sdk.store import repo
    from controlflow_sdk.store.db import connect
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
    from controlflow_sdk.store import repo
    from controlflow_sdk.store.db import connect
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
    from controlflow_sdk.plane.ingest import AdaptersUnavailable
    msg = ("Excel/Parquet support needs the optional dependencies: "
           "pip install 'controlflow-sdk[adapters]'")

    def _raise(*a, **k):
        raise AdaptersUnavailable(msg)

    monkeypatch.setattr("controlflow_sdk.plane.routes.sources.extract_table", _raise)
    resp = client.get("/sources/gone/data")
    assert resp.status_code == 200  # friendly degrade, never a 500
    assert "controlflow-sdk[adapters]" in resp.text


def test_unsupported_xls_rejected(client):
    resp = client.post("/sources",
                       data={"source_id": "old", "as_of_date": "2026-01-01"},
                       files={"file": ("old.xls", io.BytesIO(b"x"), "application/vnd.ms-excel")},
                       follow_redirects=False)
    assert resp.status_code == 200  # re-renders the form, not a redirect
    assert ".xls" in resp.text or "not supported" in resp.text.lower()
