from __future__ import annotations

import io

import pandas as pd
import pytest

from controlflow_sdk.plane import ingest


def test_extract_table_csv_stdlib():
    raw = b"id,amount\nA,5\nB,6\n"
    t = ingest.extract_table(raw, "csv")
    assert t.header == ["id", "amount"]
    assert t.rows == [["A", "5"], ["B", "6"]]
    assert t.sheet_names == []


def test_extract_table_xlsx_rows_and_sheets():
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame({"id": ["A"], "amount": [5]}).to_excel(xw, sheet_name="S1", index=False)
        pd.DataFrame({"id": ["Z"], "amount": [9]}).to_excel(xw, sheet_name="S2", index=False)
    t = ingest.extract_table(buf.getvalue(), "xlsx", sheet="S2")
    assert t.header == ["id", "amount"]
    assert t.rows == [["Z", "9"]]
    assert t.sheet_names == ["S1", "S2"]


def test_extract_table_missing_adapters_is_friendly(monkeypatch):
    def boom(*a, **k):
        raise ImportError("Missing optional dependency 'openpyxl'")
    monkeypatch.setattr("controlflow_sdk.adapters.inspect.sheet_names", boom)
    monkeypatch.setattr("controlflow_sdk.adapters.inspect.read_dataframe", boom)
    with pytest.raises(ingest.AdaptersUnavailable) as exc:
        ingest.extract_table(b"\x00\x01", "xlsx")
    assert "controlflow-sdk[adapters]" in str(exc.value)
