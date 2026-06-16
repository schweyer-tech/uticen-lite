"""Tests for the file adapter: source_for factory and CsvSource."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from controlflow_sdk.adapters.base import Source
from controlflow_sdk.adapters.files import UnsupportedSourceError, source_for
from controlflow_sdk.model.control import SourceBinding
from controlflow_sdk.model.population import Population

FIXTURES = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

_GL_COLUMN_MAPPINGS = [
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
]


def _make_binding(
    fmt: str = "csv",
    source_type: str = "file",
    path: str = "gl.csv",
) -> SourceBinding:
    """Build a minimal SourceBinding for the GL fixture."""
    return SourceBinding(
        id="gl",
        type=source_type,
        config={"format": fmt, "path": path},
        key_config={"type": "single", "columns": ["entry_id"]},
        column_mappings=_GL_COLUMN_MAPPINGS,
    )


# ---------------------------------------------------------------------------
# source_for factory
# ---------------------------------------------------------------------------


class TestSourceFor:
    def test_returns_csv_source_for_csv_binding(self) -> None:
        from controlflow_sdk.adapters.files import CsvSource

        binding = _make_binding(fmt="csv")
        src = source_for(binding, FIXTURES)
        assert isinstance(src, CsvSource)

    def test_csv_source_is_source_abc(self) -> None:
        src = source_for(_make_binding(fmt="csv"), FIXTURES)
        assert isinstance(src, Source)

    def test_raises_for_unsupported_format(self) -> None:
        binding = _make_binding(fmt="jsonl")
        with pytest.raises(UnsupportedSourceError):
            source_for(binding, FIXTURES)

    def test_raises_for_unsupported_type(self) -> None:
        binding = _make_binding(source_type="database")
        with pytest.raises(UnsupportedSourceError):
            source_for(binding, FIXTURES)


# ---------------------------------------------------------------------------
# CsvSource.load()
# ---------------------------------------------------------------------------


class TestCsvSourceLoad:
    def setup_method(self) -> None:
        self.binding = _make_binding()
        self.src = source_for(self.binding, FIXTURES)

    def test_returns_population(self) -> None:
        pop = self.src.load()
        assert isinstance(pop, Population)

    def test_row_count(self) -> None:
        pop = self.src.load()
        assert pop.size == 3

    def test_key_columns(self) -> None:
        pop = self.src.load()
        assert pop.key_columns == ["entry_id"]

    def test_source_id_matches_binding(self) -> None:
        pop = self.src.load()
        assert pop.source_id == "gl"

    def test_columns_keyed_by_original_name(self) -> None:
        pop = self.src.load()
        col_names = [c.original_name for c in pop.columns]
        assert "entry_id" in col_names
        assert "account" in col_names
        assert "amount" in col_names
        assert "posted_date" in col_names

    def test_excluded_columns_dropped(self) -> None:
        """Columns with include=False should not appear in the population."""
        mappings = [
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
                "include": False,  # excluded
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
        ]
        binding = SourceBinding(
            id="gl",
            type="file",
            config={"format": "csv", "path": "gl.csv"},
            key_config={"type": "single", "columns": ["entry_id"]},
            column_mappings=mappings,
        )
        src = source_for(binding, FIXTURES)
        pop = src.load()
        col_names = [c.original_name for c in pop.columns]
        assert "account" not in col_names
        assert "amount" in col_names

    def test_amount_coerced_to_numeric(self) -> None:
        pop = self.src.load()
        assert pd.api.types.is_numeric_dtype(pop.df["amount"])

    def test_posted_date_coerced_to_datetime(self) -> None:
        pop = self.src.load()
        assert pd.api.types.is_datetime64_any_dtype(pop.df["posted_date"])


# ---------------------------------------------------------------------------
# CsvSource.provenance()
# ---------------------------------------------------------------------------


class TestCsvSourceProvenance:
    def setup_method(self) -> None:
        self.src = source_for(_make_binding(), FIXTURES)

    def test_provenance_has_required_keys(self) -> None:
        prov = self.src.provenance()
        assert "path" in prov
        assert "sha256" in prov
        assert "row_count" in prov

    def test_sha256_is_64_char_hex(self) -> None:
        prov = self.src.provenance()
        sha = prov["sha256"]
        assert len(sha) == 64
        assert all(c in "0123456789abcdef" for c in sha)

    def test_row_count_equals_3(self) -> None:
        prov = self.src.provenance()
        assert prov["row_count"] == 3


# ---------------------------------------------------------------------------
# Source.binding()
# ---------------------------------------------------------------------------


class TestSourceBinding:
    def test_binding_delegates_to_source_binding(self) -> None:
        binding = _make_binding()
        src = source_for(binding, FIXTURES)
        result = src.binding()
        expected = binding.to_data_source()
        assert result == expected

    def test_binding_contains_type_and_key_config(self) -> None:
        src = source_for(_make_binding(), FIXTURES)
        b = src.binding()
        assert b["type"] == "file"
        assert "key_config" in b
        assert "column_mappings" in b
