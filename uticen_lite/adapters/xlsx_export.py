"""Write pipeline step data to ``.xlsx`` for local inspection (NOT the bundle).

This is a localhost-only evidence export: raw population rows are written to a workbook the
author downloads. It never touches the import bundle or the store (cardinal rule, learning
0001). Requires the ``[adapters]`` extra (``openpyxl``); a missing engine becomes a friendly
:class:`uticen_lite.plane.ingest.AdaptersUnavailable` (learning 0024).
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

import pandas as pd

from uticen_lite.plane.ingest import AdaptersUnavailable

# A worksheet holds 1_048_576 rows incl. the header row → this many DATA rows.
EXCEL_MAX_DATA_ROWS = 1_048_575
_ILLEGAL_SHEET = set("[]:*?/\\")


def _require_writer() -> None:
    """Raise a friendly error if the xlsx engine isn't installed."""
    try:
        import openpyxl  # type: ignore[import-untyped]  # noqa: F401
    except ImportError as exc:  # learning 0024 — catch ImportError first, typed re-raise
        raise AdaptersUnavailable(
            "Excel export needs the [adapters] extra. Install with: "
            "pip install 'uticen-lite[adapters]'"
        ) from exc


def _sanitize_sheet_name(name: str, used: set[str]) -> str:
    """An Excel-legal, ≤31-char, unique sheet name."""
    clean = "".join("_" if ch in _ILLEGAL_SHEET else ch for ch in name).strip() or "sheet"
    clean = clean[:31]
    base, i = clean, 2
    while clean.lower() in used:
        suffix = f"_{i}"
        clean = base[: 31 - len(suffix)] + suffix
        i += 1
    used.add(clean.lower())
    return clean


def _coerce_for_excel(frame: pd.DataFrame) -> pd.DataFrame:
    """Make every cell openpyxl-writable, keeping native numbers/dates (learning 0020)."""
    import numpy as np

    def cell(v: Any) -> Any:
        if v is None or v is pd.NaT or v is pd.NA:
            return None
        if isinstance(v, pd.Timestamp):
            return None if pd.isna(v) else v.to_pydatetime()
        if isinstance(v, (list, dict, set, tuple)):
            return str(v)
        if isinstance(v, np.generic):
            scalar = v.item()
            return None if isinstance(scalar, float) and pd.isna(scalar) else scalar
        try:
            if v is None or (np.isscalar(v) and pd.isna(v)):  # type: ignore[arg-type]
                return None
        except (TypeError, ValueError):
            pass
        return v

    out = frame.copy()
    for col in out.columns:
        # Use a list comprehension + object dtype so None cells are preserved as None
        # (Series.map on typed columns — e.g. datetime64 — would re-cast None back to NaT).
        out[col] = pd.Series([cell(v) for v in out[col]], dtype=object, index=out.index)
    return out


def _prep(frame: pd.DataFrame) -> tuple[pd.DataFrame, bool, int]:
    """Return (excel-ready frame capped to the row limit, truncated?, true total)."""
    total = len(frame)
    truncated = total > EXCEL_MAX_DATA_ROWS
    capped = frame.iloc[:EXCEL_MAX_DATA_ROWS] if truncated else frame
    return _coerce_for_excel(capped), truncated, total


def write_single_step(frame: pd.DataFrame, label: str) -> bytes:
    """A one-sheet workbook of *frame* (the data at one step)."""
    _require_writer()
    coerced, truncated, total = _prep(frame)
    used: set[str] = set()
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        sheet = _sanitize_sheet_name(label, used)
        coerced.to_excel(xw, sheet_name=sheet, index=False)
        if truncated:
            note_sheet = _sanitize_sheet_name("Truncated", used)
            pd.DataFrame(
                {"note": [f"Truncated to {EXCEL_MAX_DATA_ROWS:,} of {total:,} rows (Excel limit)."]}
            ).to_excel(xw, sheet_name=note_sheet, index=False)
    return buf.getvalue()


def write_step_workbook(steps: list[tuple[str, pd.DataFrame]], meta: dict[str, str]) -> bytes:
    """A multi-sheet workbook: one sheet per step (flow order) + Summary + About.

    *steps* is ``[(label, frame), ...]``; *meta* is shown on the About sheet
    (control id, generation timestamp, etc.).
    """
    _require_writer()
    used: set[str] = {"summary", "about"}
    prepared: list[tuple[str, pd.DataFrame]] = []
    summary_rows: list[dict[str, Any]] = []
    for i, (label, frame) in enumerate(steps, start=1):
        coerced, truncated, total = _prep(frame)
        sheet = _sanitize_sheet_name(f"{i} - {label}", used)
        prepared.append((sheet, coerced))
        summary_rows.append(
            {
                "step": i,
                "sheet": sheet,
                "label": label,
                "rows": total,
                "truncated": "yes" if truncated else "",
            }
        )

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame(
            summary_rows, columns=["step", "sheet", "label", "rows", "truncated"]
        ).to_excel(xw, sheet_name="Summary", index=False)
        pd.DataFrame(list(meta.items()), columns=["field", "value"]).to_excel(
            xw, sheet_name="About", index=False
        )
        for sheet, coerced in prepared:
            coerced.to_excel(xw, sheet_name=sheet, index=False)
    return buf.getvalue()
