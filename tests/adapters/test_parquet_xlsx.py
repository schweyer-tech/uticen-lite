"""Tests for ParquetSource and XlsxSource adapters."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

from uticen_lite.adapters.base import Source
from uticen_lite.adapters.files import source_for
from uticen_lite.model.control import SourceBinding
from uticen_lite.model.population import Population

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_GL_DATA = {
    "entry_id": ["GL-001", "GL-002", "GL-003"],
    "account": ["1000", "2000", "3000"],
    "amount": [150.0, 75.0, 300.0],
    "posted_date": ["2024-01-15", "2024-01-16", "2024-01-17"],
    "notes": ["note1", "note2", "note3"],  # extra column; excluded below
}

_GL_COLUMN_MAPPINGS: list[dict[str, Any]] = [
    {
        "original_name": "entry_id",
        "display_name": "Entry ID",
        "data_type": "text",
        "is_key": True,
        "include": True,
    },
    {
        "original_name": "account",
        "display_name": "Account",
        "data_type": "text",
        "is_key": False,
        "include": True,
    },
    {
        "original_name": "amount",
        "display_name": "Amount",
        "data_type": "number",
        "is_key": False,
        "include": True,
    },
    {
        "original_name": "posted_date",
        "display_name": "Posted Date",
        "data_type": "date",
        "is_key": False,
        "include": True,
    },
    {
        "original_name": "notes",
        "display_name": "Notes",
        "data_type": "text",
        "is_key": False,
        "include": False,  # excluded — must not appear in population
    },
]


def _make_parquet_file(tmp_path: Path) -> Path:
    """Write a parquet fixture and return its path."""
    df = pd.DataFrame(_GL_DATA)
    path = tmp_path / "gl.parquet"
    df.to_parquet(path, index=False)
    return path


def _make_xlsx_file(tmp_path: Path, sheet: str = "Sheet1") -> Path:
    """Write an xlsx fixture and return its path."""
    df = pd.DataFrame(_GL_DATA)
    path = tmp_path / "gl.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet, index=False)
    return path


def _make_binding(
    fmt: str,
    path: str,
    extra_config: dict[str, Any] | None = None,
) -> SourceBinding:
    config: dict[str, Any] = {"format": fmt, "path": path}
    if extra_config:
        config.update(extra_config)
    return SourceBinding(
        id="gl",
        type="file",
        config=config,
        key_config={"type": "single", "columns": ["entry_id"]},
        column_mappings=_GL_COLUMN_MAPPINGS,
    )


# ---------------------------------------------------------------------------
# factory tests
# ---------------------------------------------------------------------------


class TestSourceForNewFormats:
    def test_parquet_returns_parquet_source(self, tmp_path: Path) -> None:
        from uticen_lite.adapters.files import ParquetSource

        _make_parquet_file(tmp_path)
        binding = _make_binding("parquet", "gl.parquet")
        src = source_for(binding, tmp_path)
        assert isinstance(src, ParquetSource)

    def test_xlsx_returns_xlsx_source(self, tmp_path: Path) -> None:
        from uticen_lite.adapters.files import XlsxSource

        _make_xlsx_file(tmp_path)
        binding = _make_binding("xlsx", "gl.xlsx")
        src = source_for(binding, tmp_path)
        assert isinstance(src, XlsxSource)

    def test_parquet_source_is_source_abc(self, tmp_path: Path) -> None:
        _make_parquet_file(tmp_path)
        src = source_for(_make_binding("parquet", "gl.parquet"), tmp_path)
        assert isinstance(src, Source)

    def test_xlsx_source_is_source_abc(self, tmp_path: Path) -> None:
        _make_xlsx_file(tmp_path)
        src = source_for(_make_binding("xlsx", "gl.xlsx"), tmp_path)
        assert isinstance(src, Source)


# ---------------------------------------------------------------------------
# ParquetSource.load()
# ---------------------------------------------------------------------------


class TestParquetSourceLoad:
    def test_returns_population(self, tmp_path: Path) -> None:
        _make_parquet_file(tmp_path)
        src = source_for(_make_binding("parquet", "gl.parquet"), tmp_path)
        assert isinstance(src.load(), Population)

    def test_row_count(self, tmp_path: Path) -> None:
        _make_parquet_file(tmp_path)
        src = source_for(_make_binding("parquet", "gl.parquet"), tmp_path)
        assert src.load().size == 3

    def test_source_id(self, tmp_path: Path) -> None:
        _make_parquet_file(tmp_path)
        src = source_for(_make_binding("parquet", "gl.parquet"), tmp_path)
        assert src.load().source_id == "gl"

    def test_key_columns(self, tmp_path: Path) -> None:
        _make_parquet_file(tmp_path)
        src = source_for(_make_binding("parquet", "gl.parquet"), tmp_path)
        assert src.load().key_columns == ["entry_id"]

    def test_included_columns_present(self, tmp_path: Path) -> None:
        _make_parquet_file(tmp_path)
        src = source_for(_make_binding("parquet", "gl.parquet"), tmp_path)
        pop = src.load()
        col_names = [c.original_name for c in pop.columns]
        assert "entry_id" in col_names
        assert "account" in col_names
        assert "amount" in col_names
        assert "posted_date" in col_names

    def test_excluded_column_dropped(self, tmp_path: Path) -> None:
        _make_parquet_file(tmp_path)
        src = source_for(_make_binding("parquet", "gl.parquet"), tmp_path)
        pop = src.load()
        col_names = [c.original_name for c in pop.columns]
        assert "notes" not in col_names

    def test_amount_coerced_to_numeric(self, tmp_path: Path) -> None:
        _make_parquet_file(tmp_path)
        src = source_for(_make_binding("parquet", "gl.parquet"), tmp_path)
        pop = src.load()
        assert pd.api.types.is_numeric_dtype(pop.df["amount"])

    def test_posted_date_coerced_to_datetime(self, tmp_path: Path) -> None:
        _make_parquet_file(tmp_path)
        src = source_for(_make_binding("parquet", "gl.parquet"), tmp_path)
        pop = src.load()
        assert pd.api.types.is_datetime64_any_dtype(pop.df["posted_date"])


# ---------------------------------------------------------------------------
# ParquetSource.provenance()
# ---------------------------------------------------------------------------


class TestParquetSourceProvenance:
    def test_provenance_has_required_keys(self, tmp_path: Path) -> None:
        _make_parquet_file(tmp_path)
        src = source_for(_make_binding("parquet", "gl.parquet"), tmp_path)
        prov = src.provenance()
        assert "path" in prov
        assert "sha256" in prov
        assert "row_count" in prov

    def test_sha256_is_64_char_hex(self, tmp_path: Path) -> None:
        _make_parquet_file(tmp_path)
        src = source_for(_make_binding("parquet", "gl.parquet"), tmp_path)
        prov = src.provenance()
        assert len(prov["sha256"]) == 64
        assert all(c in "0123456789abcdef" for c in prov["sha256"])

    def test_sha256_matches_raw_file_bytes(self, tmp_path: Path) -> None:
        p = _make_parquet_file(tmp_path)
        expected = hashlib.sha256(p.read_bytes()).hexdigest()
        src = source_for(_make_binding("parquet", "gl.parquet"), tmp_path)
        assert src.provenance()["sha256"] == expected

    def test_row_count_equals_data_rows(self, tmp_path: Path) -> None:
        _make_parquet_file(tmp_path)
        src = source_for(_make_binding("parquet", "gl.parquet"), tmp_path)
        assert src.provenance()["row_count"] == 3

    def test_row_count_cached_after_load(self, tmp_path: Path) -> None:
        """provenance() after load() must not re-read the file for row_count."""
        _make_parquet_file(tmp_path)
        src = source_for(_make_binding("parquet", "gl.parquet"), tmp_path)
        pop = src.load()
        prov = src.provenance()
        assert prov["row_count"] == pop.size


# ---------------------------------------------------------------------------
# XlsxSource.load()
# ---------------------------------------------------------------------------


class TestXlsxSourceLoad:
    def test_returns_population(self, tmp_path: Path) -> None:
        _make_xlsx_file(tmp_path)
        src = source_for(_make_binding("xlsx", "gl.xlsx"), tmp_path)
        assert isinstance(src.load(), Population)

    def test_row_count(self, tmp_path: Path) -> None:
        _make_xlsx_file(tmp_path)
        src = source_for(_make_binding("xlsx", "gl.xlsx"), tmp_path)
        assert src.load().size == 3

    def test_source_id(self, tmp_path: Path) -> None:
        _make_xlsx_file(tmp_path)
        src = source_for(_make_binding("xlsx", "gl.xlsx"), tmp_path)
        assert src.load().source_id == "gl"

    def test_key_columns(self, tmp_path: Path) -> None:
        _make_xlsx_file(tmp_path)
        src = source_for(_make_binding("xlsx", "gl.xlsx"), tmp_path)
        assert src.load().key_columns == ["entry_id"]

    def test_included_columns_present(self, tmp_path: Path) -> None:
        _make_xlsx_file(tmp_path)
        src = source_for(_make_binding("xlsx", "gl.xlsx"), tmp_path)
        pop = src.load()
        col_names = [c.original_name for c in pop.columns]
        assert "entry_id" in col_names
        assert "account" in col_names
        assert "amount" in col_names
        assert "posted_date" in col_names

    def test_excluded_column_dropped(self, tmp_path: Path) -> None:
        _make_xlsx_file(tmp_path)
        src = source_for(_make_binding("xlsx", "gl.xlsx"), tmp_path)
        pop = src.load()
        col_names = [c.original_name for c in pop.columns]
        assert "notes" not in col_names

    def test_amount_coerced_to_numeric(self, tmp_path: Path) -> None:
        _make_xlsx_file(tmp_path)
        src = source_for(_make_binding("xlsx", "gl.xlsx"), tmp_path)
        pop = src.load()
        assert pd.api.types.is_numeric_dtype(pop.df["amount"])

    def test_posted_date_coerced_to_datetime(self, tmp_path: Path) -> None:
        _make_xlsx_file(tmp_path)
        src = source_for(_make_binding("xlsx", "gl.xlsx"), tmp_path)
        pop = src.load()
        assert pd.api.types.is_datetime64_any_dtype(pop.df["posted_date"])

    def test_reads_named_sheet(self, tmp_path: Path) -> None:
        """config["sheet"] routes to the correct worksheet."""
        df = pd.DataFrame(_GL_DATA)
        path = tmp_path / "multi.xlsx"
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            pd.DataFrame({"x": [1]}).to_excel(writer, sheet_name="Ignore", index=False)
            df.to_excel(writer, sheet_name="GL", index=False)

        binding = _make_binding("xlsx", "multi.xlsx", extra_config={"sheet": "GL"})
        src = source_for(binding, tmp_path)
        pop = src.load()
        assert pop.size == 3

    def test_defaults_to_first_sheet_when_sheet_not_specified(self, tmp_path: Path) -> None:
        """When config has no 'sheet' key, the first sheet is used."""
        _make_xlsx_file(tmp_path, sheet="Data")
        src = source_for(_make_binding("xlsx", "gl.xlsx"), tmp_path)
        assert src.load().size == 3


# ---------------------------------------------------------------------------
# XlsxSource.provenance()
# ---------------------------------------------------------------------------


class TestXlsxSourceProvenance:
    def test_provenance_has_required_keys(self, tmp_path: Path) -> None:
        _make_xlsx_file(tmp_path)
        src = source_for(_make_binding("xlsx", "gl.xlsx"), tmp_path)
        prov = src.provenance()
        assert "path" in prov
        assert "sha256" in prov
        assert "row_count" in prov

    def test_sha256_is_64_char_hex(self, tmp_path: Path) -> None:
        _make_xlsx_file(tmp_path)
        src = source_for(_make_binding("xlsx", "gl.xlsx"), tmp_path)
        prov = src.provenance()
        assert len(prov["sha256"]) == 64
        assert all(c in "0123456789abcdef" for c in prov["sha256"])

    def test_sha256_matches_raw_file_bytes(self, tmp_path: Path) -> None:
        p = _make_xlsx_file(tmp_path)
        expected = hashlib.sha256(p.read_bytes()).hexdigest()
        src = source_for(_make_binding("xlsx", "gl.xlsx"), tmp_path)
        assert src.provenance()["sha256"] == expected

    def test_row_count_equals_data_rows(self, tmp_path: Path) -> None:
        _make_xlsx_file(tmp_path)
        src = source_for(_make_binding("xlsx", "gl.xlsx"), tmp_path)
        assert src.provenance()["row_count"] == 3

    def test_row_count_cached_after_load(self, tmp_path: Path) -> None:
        _make_xlsx_file(tmp_path)
        src = source_for(_make_binding("xlsx", "gl.xlsx"), tmp_path)
        pop = src.load()
        prov = src.provenance()
        assert prov["row_count"] == pop.size
