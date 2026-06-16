"""File-based source adapters (CSV, Parquet, Excel).

This module is CPython-only — it imports pandas and optional heavy libs
(openpyxl, pyarrow) from the ``[adapters]`` extra. Do NOT import this from
pure-Python core modules.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from collections.abc import Callable
from typing import Any

import pandas as pd

from controlflow_sdk.adapters.base import Source
from controlflow_sdk.model.control import SourceBinding
from controlflow_sdk.model.population import ColumnMeta, Population

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UnsupportedSourceError(ValueError):
    """Raised when ``source_for`` cannot handle the binding's type/format."""


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------

_BOOLEAN_TRUE_VALUES = {"true", "1", "yes", "y"}
_BOOLEAN_FALSE_VALUES = {"false", "0", "no", "n"}


def coerce_series(series: pd.Series, data_type: str) -> pd.Series:  # type: ignore[type-arg]
    """Coerce a pandas Series to the canonical ControlFlow ``data_type``.

    Mirrors the app's data-ingest contract (learning 0029):

    ``text``
        Cast to ``str``; ``NaN`` / ``None`` become the empty string ``""``.

    ``number``
        Cast to ``float64`` via ``pd.to_numeric(errors='coerce')``;
        uncoercible values become ``NaN``.

    ``date``
        Cast to ``datetime64[ns]`` via ``pd.to_datetime(errors='coerce',
        utc=False)``; uncoercible values become ``NaT``.  Timezone info is
        stripped so comparisons stay simple (same as the app's behaviour).

    ``boolean``
        Lower-cased string comparison against a fixed set of truthy/falsy
        tokens.  Unrecognised values become ``False`` (permissive, matches the
        app's default-to-false policy for unresolved flags).
    """
    if data_type == "number":
        return pd.to_numeric(series, errors="coerce")

    if data_type == "date":
        converted = pd.to_datetime(series, errors="coerce", utc=False)
        # Strip tz to keep dtype as datetime64[ns], not datetime64[ns, tz]
        if hasattr(converted, "dt") and converted.dt.tz is not None:
            converted = converted.dt.tz_localize(None)
        return converted

    if data_type == "boolean":
        lowered = series.astype(str).str.strip().str.lower()
        return lowered.isin(_BOOLEAN_TRUE_VALUES)

    # default: "text"
    return series.where(series.isna(), series.astype(str)).fillna("")


# ---------------------------------------------------------------------------
# CsvSource
# ---------------------------------------------------------------------------


class CsvSource(Source):
    """Load a CSV file and produce a coerced :class:`Population`.

    Parameters
    ----------
    binding:
        The :class:`SourceBinding` that owns this adapter.
    root:
        Project root directory; ``binding.config["path"]`` is resolved
        relative to this.
    """

    def __init__(self, binding: SourceBinding, root: Path) -> None:
        self._binding = binding
        self._root = root
        self._path: Path = root / binding.config["path"]

    # ------------------------------------------------------------------
    # Source interface
    # ------------------------------------------------------------------

    def load(self) -> Population:
        """Read the CSV, apply column mappings, and return a :class:`Population`."""
        raw_df = pd.read_csv(self._path, dtype=str)  # read everything as str first

        # Build a lookup from original_name → mapping spec
        mapping_by_name: dict[str, dict[str, Any]] = {
            cm["original_name"]: cm for cm in self._binding.column_mappings
        }

        # Determine which key columns exist in key_config
        key_cols: set[str] = set(self._binding.key_config.get("columns", []))

        columns: list[ColumnMeta] = []
        kept_series: dict[str, pd.Series] = {}  # type: ignore[type-arg]

        for original_name, spec in mapping_by_name.items():
            if not spec.get("include", True):
                continue

            if original_name not in raw_df.columns:
                # Column declared in mappings but absent from file — skip
                continue

            data_type: str = spec.get("data_type", "text")
            coerced = coerce_series(raw_df[original_name], data_type)
            kept_series[original_name] = coerced

            is_key = original_name in key_cols
            columns.append(
                ColumnMeta(
                    original_name=original_name,
                    display_name=spec.get("display_name", original_name),
                    data_type=data_type,
                    is_key=is_key,
                    include=True,
                )
            )

        result_df = pd.DataFrame(kept_series)
        return Population(df=result_df, columns=columns, source_id=self._binding.id)

    def provenance(self) -> dict[str, Any]:
        """Return file path, SHA-256 digest of raw bytes, and row count."""
        raw_bytes = self._path.read_bytes()
        sha256 = hashlib.sha256(raw_bytes).hexdigest()

        # Row count: count non-empty lines, then subtract 1 for the header.
        # Split on newlines and filter out empty strings (covers trailing newline).
        lines = [ln for ln in raw_bytes.split(b"\n") if ln]
        row_count = max(len(lines) - 1, 0)  # subtract 1 for the header row

        return {
            "path": str(self._path),
            "sha256": sha256,
            "row_count": row_count,
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

# Callable[(binding, root)] -> Source
_SourceFactory = Callable[[SourceBinding, Path], Source]

_FILE_FORMAT_MAP: dict[str, _SourceFactory] = {
    "csv": CsvSource,
    # "parquet": ParquetSource,   # Phase 2 v2
    # "xlsx": ExcelSource,        # Phase 2 v2
}

_SUPPORTED_FORMATS = {"csv", "parquet", "xlsx"}


def source_for(binding: SourceBinding, root: Path) -> Source:
    """Return the appropriate :class:`Source` for *binding*.

    Parameters
    ----------
    binding:
        A parsed :class:`SourceBinding` from ``control.yaml``.
    root:
        Project root; file paths in ``binding.config`` are resolved relative
        to this directory.

    Raises
    ------
    UnsupportedSourceError
        If ``binding.type`` is not ``"file"``, or if ``binding.config["format"]``
        is not in ``{csv, parquet, xlsx}``, or if the format is recognised but
        has no implementation yet.
    """
    if binding.type != "file":
        raise UnsupportedSourceError(
            f"Source type {binding.type!r} is not supported in v1. Only type='file' is supported."
        )

    fmt: str = binding.config.get("format", "")
    if fmt not in _SUPPORTED_FORMATS:
        raise UnsupportedSourceError(
            f"File format {fmt!r} is not supported. Supported formats: {sorted(_SUPPORTED_FORMATS)}"
        )

    cls = _FILE_FORMAT_MAP.get(fmt)
    if cls is None:
        raise UnsupportedSourceError(f"File format {fmt!r} is recognised but not yet implemented.")

    return cls(binding, root)
