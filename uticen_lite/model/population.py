from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd


@dataclass
class ColumnMeta:
    """Metadata for a column in a population."""

    original_name: str
    display_name: str
    data_type: str = "text"
    is_key: bool = False
    include: bool = True


@dataclass
class Population:
    """A population of data with column metadata."""

    df: pd.DataFrame
    columns: list[ColumnMeta]
    source_id: str

    @property
    def size(self) -> int:
        """Return the number of rows in the population."""
        return len(self.df)

    @property
    def key_columns(self) -> list[str]:
        """Return the original names of columns marked as keys."""
        return [col.original_name for col in self.columns if col.is_key]

    def key_for(self, row: Mapping) -> str:
        """
        Generate a key for a row by joining key columns with '|'.

        Raises ValueError if there are no key columns.
        """
        if not self.key_columns:
            raise ValueError("Population has no key columns")
        key_parts = [str(row[col_name]) for col_name in self.key_columns]
        return "|".join(key_parts)
