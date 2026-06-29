from __future__ import annotations

import io

import pandas as pd

from uticen_lite.adapters import inspect


def _xlsx_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False)
    return buf.getvalue()


def test_read_dataframe_xlsx_default_and_named_sheet():
    raw = _xlsx_bytes({
        "First": pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}),
        "Second": pd.DataFrame({"a": [9], "b": ["z"]}),
    })
    assert inspect.sheet_names(raw) == ["First", "Second"]
    # default -> first sheet
    df0 = inspect.read_dataframe(raw, "xlsx")
    assert list(df0.columns) == ["a", "b"] and len(df0) == 2
    # named -> second sheet
    df2 = inspect.read_dataframe(raw, "xlsx", sheet="Second")
    assert len(df2) == 1 and df2.iloc[0]["b"] == "z"


def test_read_dataframe_parquet_roundtrip():
    raw_buf = io.BytesIO()
    pd.DataFrame({"id": ["A", "B"], "n": [1, 2]}).to_parquet(raw_buf, index=False)
    df = inspect.read_dataframe(raw_buf.getvalue(), "parquet")
    assert list(df.columns) == ["id", "n"] and len(df) == 2
