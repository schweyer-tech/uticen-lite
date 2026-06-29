"""File-based source adapters (CSV, Parquet, Excel).

This module is CPython-only — it imports pandas and optional heavy libs
(openpyxl, pyarrow) from the ``[adapters]`` extra. Do NOT import this from
pure-Python core modules.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from uticen_lite.adapters.base import Source
from uticen_lite.model.control import SourceBinding
from uticen_lite.model.population import ColumnMeta, Population

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
    """Coerce a pandas Series to the canonical Uticen ``data_type``.

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
# Shared helpers (DRY across CsvSource / ParquetSource / XlsxSource)
# ---------------------------------------------------------------------------


def _hash_file(path: Path) -> str:
    """Return the SHA-256 hex digest of the raw bytes of *path*."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _apply_mappings(
    raw_df: pd.DataFrame,
    column_mappings: list[dict[str, Any]],
    key_config: dict[str, Any],
    source_id: str,
) -> Population:
    """Apply column mappings + coercions to *raw_df* and return a :class:`Population`.

    This is the single implementation of the mapping/coercion contract shared
    by all file-based sources (CSV, Parquet, Excel).  Each source calls this
    after loading its raw DataFrame.

    Parameters
    ----------
    raw_df:
        The DataFrame as returned by the format-specific reader.  Column
        names must match ``original_name`` values in *column_mappings*.
    column_mappings:
        List of column-mapping dicts from :class:`SourceBinding`.
    key_config:
        Key configuration dict; ``key_config["columns"]`` is the list of
        ``original_name`` values that form the row key.
    source_id:
        The binding id, forwarded to :class:`Population`.
    """
    mapping_by_name: dict[str, dict[str, Any]] = {cm["original_name"]: cm for cm in column_mappings}
    key_cols: set[str] = set(key_config.get("columns", []))

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
    return Population(df=result_df, columns=columns, source_id=source_id)


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
        self._path: Path = root / binding.config["path"]
        # Populated by load(); provenance() falls back to parsing if not yet set.
        self._row_count: int | None = None

    # ------------------------------------------------------------------
    # Source interface
    # ------------------------------------------------------------------

    def load(self) -> Population:
        """Read the CSV, apply column mappings, and return a :class:`Population`."""
        raw_df = pd.read_csv(self._path, dtype=str)  # read everything as str first
        pop = _apply_mappings(
            raw_df, self._binding.column_mappings, self._binding.key_config, self._binding.id
        )
        self._row_count = pop.size
        return pop

    def provenance(self) -> dict[str, Any]:
        """Return file path, SHA-256 digest of raw bytes, and row count.

        ``sha256`` is computed from the raw file bytes (integrity of the
        original file).  ``row_count`` reflects the number of DATA rows
        actually parsed by pandas — it equals ``len(df)`` and correctly
        handles quoted fields that contain embedded newlines, trailing
        newlines, or any other formatting artefact that would fool a naive
        line-count approach.

        If :meth:`load` has already been called the cached count is reused;
        otherwise the file is parsed once to obtain the count.
        """
        sha256 = _hash_file(self._path)

        if self._row_count is None:
            # load() hasn't been called yet — parse minimally just to count rows.
            self._row_count = len(pd.read_csv(self._path, dtype=str))

        return {
            "path": self._binding.config["path"],
            "sha256": sha256,
            "row_count": self._row_count,
        }


# ---------------------------------------------------------------------------
# ParquetSource
# ---------------------------------------------------------------------------


class ParquetSource(Source):
    """Load a Parquet file and produce a coerced :class:`Population`.

    Requires the ``[adapters]`` extra (``pyarrow``).

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
        self._path: Path = root / binding.config["path"]
        self._row_count: int | None = None

    def load(self) -> Population:
        """Read the Parquet file, apply column mappings, and return a :class:`Population`."""
        raw_df = pd.read_parquet(self._path)
        pop = _apply_mappings(
            raw_df, self._binding.column_mappings, self._binding.key_config, self._binding.id
        )
        self._row_count = pop.size
        return pop

    def provenance(self) -> dict[str, Any]:
        """Return file path, SHA-256 digest of raw bytes, and row count.

        ``sha256`` is the digest of the raw Parquet file bytes.  ``row_count``
        is the number of data rows (len of loaded DataFrame), cached after the
        first call to :meth:`load`.
        """
        sha256 = _hash_file(self._path)

        if self._row_count is None:
            self._row_count = len(pd.read_parquet(self._path))

        return {
            "path": self._binding.config["path"],
            "sha256": sha256,
            "row_count": self._row_count,
        }


# ---------------------------------------------------------------------------
# XlsxSource
# ---------------------------------------------------------------------------


class XlsxSource(Source):
    """Load an Excel (xlsx) file and produce a coerced :class:`Population`.

    Requires the ``[adapters]`` extra (``openpyxl``).

    Parameters
    ----------
    binding:
        The :class:`SourceBinding` that owns this adapter.  An optional
        ``binding.config["sheet"]`` selects the worksheet; when absent the
        first sheet is used.
    root:
        Project root directory; ``binding.config["path"]`` is resolved
        relative to this.
    """

    def __init__(self, binding: SourceBinding, root: Path) -> None:
        self._binding = binding
        self._path: Path = root / binding.config["path"]
        self._sheet: str | int = binding.config.get("sheet", 0)
        self._row_count: int | None = None

    def load(self) -> Population:
        """Read the xlsx file, apply column mappings, and return a :class:`Population`."""
        raw_df = pd.read_excel(self._path, sheet_name=self._sheet, engine="openpyxl")
        pop = _apply_mappings(
            raw_df, self._binding.column_mappings, self._binding.key_config, self._binding.id
        )
        self._row_count = pop.size
        return pop

    def provenance(self) -> dict[str, Any]:
        """Return file path, SHA-256 digest of raw bytes, and row count.

        ``sha256`` is the digest of the raw xlsx file bytes.  ``row_count``
        is the number of data rows (len of loaded DataFrame), cached after the
        first call to :meth:`load`.
        """
        sha256 = _hash_file(self._path)

        if self._row_count is None:
            self._row_count = len(
                pd.read_excel(self._path, sheet_name=self._sheet, engine="openpyxl")
            )

        return {
            "path": self._binding.config["path"],
            "sha256": sha256,
            "row_count": self._row_count,
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

# Callable[(binding, root)] -> Source
_SourceFactory = Callable[[SourceBinding, Path], Source]

_FILE_FORMAT_MAP: dict[str, _SourceFactory] = {
    "csv": CsvSource,
    "parquet": ParquetSource,
    "xlsx": XlsxSource,
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
        is not in ``{csv, parquet, xlsx}``.
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

    cls = _FILE_FORMAT_MAP[fmt]
    return cls(binding, root)
