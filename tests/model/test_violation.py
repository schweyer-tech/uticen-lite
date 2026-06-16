import pytest

from controlflow_sdk.model.violation import Severity, Violation


def test_minimal_violation_defaults_to_medium():
    v = Violation(item_key="INV-1", description="late posting")
    assert v.severity is Severity.MEDIUM
    assert v.details == {}
    assert v.to_dict() == {
        "item_key": "INV-1",
        "description": "late posting",
        "severity": "medium",
        "details": {},
    }


def test_severity_coerce_unknown_defaults_medium():
    assert Severity.coerce("bogus") is Severity.MEDIUM
    assert Severity.coerce(None) is Severity.MEDIUM
    assert Severity.coerce("HIGH") is Severity.HIGH


def test_from_raw_missing_item_key_raises():
    with pytest.raises(ValueError, match="item_key"):
        Violation.from_raw({"description": "x"})


def test_from_raw_coerces_and_defaults():
    v = Violation.from_raw({"item_key": "A", "description": "d", "severity": "critical"})
    assert v.severity is Severity.CRITICAL
