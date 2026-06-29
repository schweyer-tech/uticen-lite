"""Test the public API surface of uticen_lite."""

from uticen_lite import ColumnMeta, Population, Severity, Violation


def test_violation_import_and_to_dict() -> None:
    """Test that Violation can be imported and has correct default severity."""
    violation = Violation("INV-123", "Amount exceeds limit")
    result = violation.to_dict()
    assert result["item_key"] == "INV-123"
    assert result["description"] == "Amount exceeds limit"
    assert result["severity"] == "medium"
    assert result["details"] == {}


def test_severity_import() -> None:
    """Test that Severity can be imported and has expected values."""
    assert Severity.LOW.value == "low"
    assert Severity.MEDIUM.value == "medium"
    assert Severity.HIGH.value == "high"
    assert Severity.CRITICAL.value == "critical"


def test_population_import() -> None:
    """Test that Population can be imported (type-only, not instantiated here)."""
    # Population is available for type hints
    assert Population is not None


def test_column_meta_import() -> None:
    """Test that ColumnMeta can be imported (type-only)."""
    # ColumnMeta is available for type hints
    assert ColumnMeta is not None
