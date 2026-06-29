"""Pandas-backed reads for source inspection (header/rows/sheets).

CPython-only — imports pandas (core dep) and, at read time, the optional
``[adapters]`` engines (openpyxl for xlsx, pyarrow for parquet). Kept under
``adapters/`` so the pure-Python core stays pandas-free (STRATEGY.md).
"""

from __future__ import annotations

import io

import pandas as pd


def read_dataframe(raw: bytes, fmt: str, *, sheet: str | int | None = None) -> pd.DataFrame:
    """Read *raw* bytes of *fmt* into a DataFrame (strings where possible)."""
    if fmt == "csv":
        return pd.read_csv(io.BytesIO(raw), dtype=str)
    if fmt == "xlsx":
        return pd.read_excel(
            io.BytesIO(raw), sheet_name=(0 if sheet is None else sheet),
            engine="openpyxl", dtype=str,
        )
    if fmt == "parquet":
        return pd.read_parquet(io.BytesIO(raw))
    raise ValueError(f"Unsupported format {fmt!r}")


def sheet_names(raw: bytes) -> list[str]:
    """Return the worksheet names of an xlsx workbook, in order."""
    return [str(name) for name in pd.ExcelFile(io.BytesIO(raw), engine="openpyxl").sheet_names]
