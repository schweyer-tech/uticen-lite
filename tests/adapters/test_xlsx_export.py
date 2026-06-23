"""xlsx step exports: sheets, summary, sanitisation, coercion, truncation."""
from __future__ import annotations

from io import BytesIO

import numpy as np
import pandas as pd
import pytest

from controlflow_sdk.adapters import xlsx_export as X


def _read(buf_bytes, sheet=0):
    return pd.read_excel(BytesIO(buf_bytes), sheet_name=sheet, engine="openpyxl")


def test_single_step_roundtrips():
    frame = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    back = _read(X.write_single_step(frame, "2 - filter"))
    assert list(back.columns) == ["id", "name"]
    assert len(back) == 2


def test_workbook_has_summary_about_and_one_sheet_per_step():
    steps = [("import", pd.DataFrame({"x": [1, 2, 3]})),
             ("filter", pd.DataFrame({"x": [2, 3]}))]
    book = X.write_step_workbook(steps, {"control": "C-1", "generated_at": "2026-06-23"})
    names = pd.ExcelFile(BytesIO(book), engine="openpyxl").sheet_names
    assert "Summary" in names and "About" in names
    assert len([n for n in names if n not in ("Summary", "About")]) == 2
    summary = _read(book, "Summary")
    assert set(summary["rows"]) == {3, 2}


def test_sheet_name_sanitised_and_deduped():
    used: set[str] = set()
    a = X._sanitize_sheet_name("a/b:c*d?e[f]" * 4, used)   # illegal chars + > 31 chars
    b = X._sanitize_sheet_name("a/b:c*d?e[f]" * 4, used)
    assert not (set("[]:*?/\\") & set(a)) and len(a) <= 31
    assert a != b                                          # deduped


def test_coercion_handles_timestamp_nat_numpy_and_objects():
    frame = pd.DataFrame({
        "ts": [pd.Timestamp("2026-01-01"), pd.NaT],
        "np": [np.int64(5), np.float64(1.5)],
        "obj": [{"k": 1}, [1, 2]],
    })
    out = X._coerce_for_excel(frame)
    # writing must not raise, and objects became strings:
    _read(X.write_single_step(frame, "s"))
    assert isinstance(out["obj"].iloc[0], str)


def test_truncation_note_when_over_excel_limit(monkeypatch):
    monkeypatch.setattr(X, "EXCEL_MAX_DATA_ROWS", 3)      # shrink the cap for the test
    steps = [("big", pd.DataFrame({"x": list(range(10))}))]
    book = X.write_step_workbook(steps, {"control": "C-1"})
    summary = _read(book, "Summary")
    assert summary.loc[0, "rows"] == 10                   # reports the TRUE total
    assert str(summary.loc[0, "truncated"]).lower() in ("yes", "true")
    sheet = pd.ExcelFile(BytesIO(book), engine="openpyxl").sheet_names
    data_sheet = [n for n in sheet if n not in ("Summary", "About")][0]
    assert len(_read(book, data_sheet)) == 3             # capped


def test_nat_and_na_coerce_to_none():
    frame = pd.DataFrame({
        "d": [pd.Timestamp("2026-01-01"), pd.NaT],
        "a": pd.array([1, pd.NA], dtype="Int64"),
    })
    out = X._coerce_for_excel(frame)
    assert out["d"].iloc[1] is None
    assert out["a"].iloc[1] is None


def test_single_step_truncation_keeps_data_sheet(monkeypatch):
    monkeypatch.setattr(X, "EXCEL_MAX_DATA_ROWS", 2)
    frame = pd.DataFrame({"x": list(range(5))})
    buf = X.write_single_step(frame, "Truncated")
    xf = pd.ExcelFile(BytesIO(buf), engine="openpyxl")
    names = xf.sheet_names
    assert len(names) == 2, f"expected 2 sheets, got {names}"
    data_sheet = names[0]
    assert len(_read(buf, data_sheet)) == 2   # capped rows present
    assert names[0] != names[1]               # distinct sheet names


def test_missing_openpyxl_raises_adapters_unavailable(monkeypatch):
    from controlflow_sdk.plane.ingest import AdaptersUnavailable

    def _boom():
        raise ImportError("no openpyxl")

    monkeypatch.setattr(X, "_require_writer", _boom)
    with pytest.raises((AdaptersUnavailable, ImportError)):
        X.write_single_step(pd.DataFrame({"x": [1]}), "s")
