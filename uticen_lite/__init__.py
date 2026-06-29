"""Uticen SDK — author and run full-population control tests."""

from uticen_lite.model.population import ColumnMeta, Population
from uticen_lite.model.violation import Severity, Violation

__version__ = "0.1.0"

__all__ = [
    "ColumnMeta",
    "Population",
    "Severity",
    "Violation",
    "__version__",
]
