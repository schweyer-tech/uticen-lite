"""Format-aware table extraction for the control plane upload/preview paths.

The single funnel that replaces CSV-hardcoded header/row parsing. CSV stays
stdlib (no [adapters] needed); xlsx/parquet lazily delegate to
``adapters.inspect`` so pandas stays confined to ``adapters/`` (STRATEGY.md).
"""

from __future__ import annotations

import csv as csvmod
import io
from dataclasses import dataclass, field


class AdaptersUnavailable(RuntimeError):
    """xlsx/parquet ingest needs the optional ``[adapters]`` extra, which is absent."""


@dataclass(frozen=True)
class ExtractedTable:
    header: list[str]
    rows: list[list[str]]
    sheet_names: list[str] = field(default_factory=list)


def extract_table(raw: bytes, fmt: str, *, sheet: str | int | None = None) -> ExtractedTable:
    """Return header + string rows (+ xlsx sheet names) for *raw* bytes of *fmt*."""
    if fmt == "csv":
        return _csv_table(raw)
    if fmt in ("xlsx", "parquet"):
        return _adapters_table(raw, fmt, sheet)
    raise ValueError(f"Unsupported format {fmt!r}")


def _csv_table(raw: bytes) -> ExtractedTable:
    all_rows = list(csvmod.reader(io.StringIO(raw.decode("utf-8-sig"))))
    if not all_rows:
        return ExtractedTable(header=[], rows=[])
    return ExtractedTable(header=all_rows[0], rows=all_rows[1:])


def _adapters_table(raw: bytes, fmt: str, sheet: str | int | None) -> ExtractedTable:
    # pandas is a core dep so the import succeeds; the engine (openpyxl/pyarrow)
    # is the optional piece and raises ImportError at READ time when absent.
    from controlflow_sdk.adapters import inspect as _inspect

    try:
        names = _inspect.sheet_names(raw) if fmt == "xlsx" else []
        df = _inspect.read_dataframe(raw, fmt, sheet=sheet)
    except ImportError as e:  # openpyxl / pyarrow missing
        raise AdaptersUnavailable(
            "Excel/Parquet support needs the optional dependencies: "
            "pip install 'controlflow-sdk[adapters]'"
        ) from e

    header = [str(c) for c in df.columns]
    filled = df.where(df.notna(), "")
    rows = [[str(v) for v in rec] for rec in filled.itertuples(index=False, name=None)]
    return ExtractedTable(header=header, rows=rows, sheet_names=names)
