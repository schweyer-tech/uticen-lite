"""End-to-end test: xlsx source on a non-default sheet runs full-population correctly.

Creates an xlsx source on the "Real" sheet (not the first sheet, which contains a
DECOY row) through the web route, then loads it via source_for and asserts that
only the Real sheet's population (U1, U2) is returned — NOT the decoy first sheet.
"""

from __future__ import annotations

import io

import pandas as pd

from uticen_lite.adapters.files import source_for
from uticen_lite.store import repo
from uticen_lite.store.db import connect


def test_xlsx_second_sheet_runs_full_population(client):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame({"user_id": ["DECOY"], "amount": ["999"]}).to_excel(
            xw, sheet_name="First", index=False
        )
        pd.DataFrame({"user_id": ["U1", "U2"], "amount": ["10", "20"]}).to_excel(
            xw, sheet_name="Real", index=False
        )
    client.post(
        "/sources",
        data={"source_id": "gl", "as_of_date": "2026-01-01", "sheet": "Real"},
        files={
            "file": (
                "gl.xlsx",
                io.BytesIO(buf.getvalue()),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        follow_redirects=False,
    )

    conn = connect(client.app.state.project_root)
    from uticen_lite.store.loader import _binding

    src = repo.get_source(conn, "gl")
    conn.close()
    pop = source_for(_binding(src), client.app.state.project_root).load()
    # Read the chosen sheet (Real), not the decoy first sheet, full population.
    assert sorted(pop.df["user_id"].tolist()) == ["U1", "U2"]
    assert "DECOY" not in pop.df["user_id"].tolist()
