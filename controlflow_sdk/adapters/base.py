"""Abstract base class for ControlFlow data source adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from controlflow_sdk.model.control import SourceBinding
from controlflow_sdk.model.population import Population


class Source(ABC):
    """Abstract adapter that loads a :class:`Population` from a bound data source.

    Subclasses are responsible for reading raw data and producing a
    fully-coerced :class:`Population` whose columns match the
    ``SourceBinding.column_mappings``.
    """

    _binding: SourceBinding

    @abstractmethod
    def load(self) -> Population:
        """Read raw data and return a coerced :class:`Population`."""

    @abstractmethod
    def provenance(self) -> dict[str, Any]:
        """Return provenance metadata for the source.

        Required keys:
          - ``path``      – str: path to the source file / resource
          - ``sha256``    – str: 64-char lowercase hex digest of raw bytes
          - ``row_count`` – int: number of data rows (excluding header)
        """

    def binding(self) -> dict[str, Any]:
        """Delegate to the bound :class:`SourceBinding` ``to_data_source()``."""
        return self._binding.to_data_source()
