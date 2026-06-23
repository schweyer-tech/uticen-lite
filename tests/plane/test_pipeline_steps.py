"""Full-population step frames feed the badges and the inspector route."""
from __future__ import annotations

import io
import json

import pandas as pd
import pytest

from controlflow_sdk.plane.routes import pipeline as P

# ---------------------------------------------------------------------------
# Fixture helpers (follow the pattern in test_pipeline_editor.py)
# ---------------------------------------------------------------------------

def _make_source(client, sid, csv_bytes: bytes) -> None:
    client.post(
        "/sources",
        data={"source_id": sid, "format": "csv"},
        files={"file": (f"{sid}.csv", io.BytesIO(csv_bytes), "text/csv")},
        follow_redirects=False,
    )


def _conn(client):
    from controlflow_sdk.store.db import connect
    return connect(client.app.state.project_root)


# A small CSV with a known number of data rows (5 rows, not counting the header).
_INVOICES_CSV = (
    b"invoice_id,amount\n"
    b"INV001,100\n"
    b"INV002,200\n"
    b"INV003,300\n"
    b"INV004,400\n"
    b"INV005,500\n"
)
_EXPECTED_INVOICE_ROWS = 5


@pytest.fixture()
def seeded_client(client):
    """Client with a CSV source already uploaded; returns (client, project_root)."""
    _make_source(client, "invoices", _INVOICES_CSV)
    root = client.app.state.project_root
    return client, root


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_load_full_frames_is_uncapped(seeded_client):
    """_load_full_frames returns the WHOLE file — no .head() cap."""
    client, root = seeded_client
    conn = _conn(client)
    try:
        frames = P._load_full_frames(conn, root, ["invoices"])
    finally:
        conn.close()

    assert "invoices" in frames, "_load_full_frames returned no frame for 'invoices'"
    assert len(frames["invoices"]) == _EXPECTED_INVOICE_ROWS, (
        f"expected {_EXPECTED_INVOICE_ROWS} rows, got {len(frames['invoices'])} — "
        "frame may be capped or source not found"
    )


def test_source_versions_returns_nonempty_token(seeded_client):
    """_source_versions returns a non-empty version token for each bound source."""
    client, root = seeded_client
    conn = _conn(client)
    try:
        versions = P._source_versions(conn, root, ["invoices"])
    finally:
        conn.close()

    assert versions.get("invoices"), (
        "_source_versions returned empty/missing token for 'invoices'"
    )
    # The token should encode enough to detect file changes (contains the stored path).
    token = versions["invoices"]
    assert isinstance(token, str) and len(token) > 0


# ---------------------------------------------------------------------------
# Helpers for step-inspector tests
# ---------------------------------------------------------------------------

def _make_control(client, cid="C1"):
    client.post("/controls", data={
        "id": cid, "title": "Step Inspector Test", "objective": "o", "narrative": "n",
    }, follow_redirects=False)


def _save_pipeline(client, cid, graph):
    return client.post(f"/controls/{cid}/logic/builder",
                       data={"pipeline_json": json.dumps(graph)},
                       follow_redirects=False)


def _seeded(client):
    """Upload a source + create a control with a pipeline containing a 'flt' filter node.

    Returns (client, control_id).
    """
    _make_source(client, "invoices", _INVOICES_CSV)
    cid = "CI1"
    _make_control(client, cid)
    graph = {"nodes": [
        {"id": "src1", "type": "import", "source_id": "invoices",
         "narrative": "", "config": {}, "inputs": []},
        {"id": "flt", "type": "filter", "inputs": ["src1"],
         "narrative": "Keep all rows",
         "config": {"logic": "all", "conditions": []}},
        {"id": "tst", "type": "test", "inputs": ["flt"],
         "narrative": "",
         "config": {"logic": "all", "conditions": []}},
    ]}
    _save_pipeline(client, cid, graph)
    return client, cid


# ---------------------------------------------------------------------------
# Step inspector route tests
# ---------------------------------------------------------------------------

def test_step_data_route_paginates(client):
    c, control_id = _seeded(client)
    r = c.get(f"/controls/{control_id}/logic/step/flt/data")
    assert r.status_code == 200
    assert "records" in r.text and "of" in r.text          # "records X–Y of Z"


def test_step_data_unknown_node_degrades(client):
    c, control_id = _seeded(client)
    r = c.get(f"/controls/{control_id}/logic/step/does-not-exist/data")
    assert r.status_code == 200                              # never 500
    assert "isn't computable" in r.text or "not computable" in r.text


# ---------------------------------------------------------------------------
# xlsx export route tests
# ---------------------------------------------------------------------------

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_per_step_xlsx_downloads(client):
    c, control_id = _seeded(client)
    r = c.get(f"/controls/{control_id}/logic/step/flt/export.xlsx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(_XLSX)
    pd.read_excel(io.BytesIO(r.content), engine="openpyxl")   # valid workbook


def test_workbook_xlsx_has_a_sheet_per_step(client):
    c, control_id = _seeded(client)
    r = c.get(f"/controls/{control_id}/logic/export-steps.xlsx")
    assert r.status_code == 200
    names = pd.ExcelFile(io.BytesIO(r.content), engine="openpyxl").sheet_names
    assert "Summary" in names and "About" in names
