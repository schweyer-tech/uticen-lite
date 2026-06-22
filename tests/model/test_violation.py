import json

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


def test_from_raw_makes_details_json_safe():
    """Details from a no-code rule on a date/number/bool column carry pandas and
    numpy scalars (Timestamp, NaT, np.bool_, np.int64, NaN). from_raw must coerce
    them to JSON-native values so the run can be persisted and rendered.
    """
    pd = pytest.importorskip("pandas")
    np = pytest.importorskip("numpy")

    v = Violation.from_raw(
        {
            "item_key": "ACC-1",
            "description": "stale review",
            "details": {
                "last_review_date": pd.Timestamp("2025-10-15"),
                "missing_date": pd.NaT,
                "is_privileged": np.bool_(True),
                "count": np.int64(7),
                "ratio": np.float64(1.5),
                "missing_num": float("nan"),
                "approver": "emp-016",
            },
        }
    )

    assert v.details == {
        "last_review_date": "2025-10-15T00:00:00",
        "missing_date": None,
        "is_privileged": True,
        "count": 7,
        "ratio": 1.5,
        "missing_num": None,
        "approver": "emp-016",
    }
    # The whole violation must round-trip through JSON (the run/store boundary).
    assert json.loads(json.dumps(v.to_dict()))["details"]["count"] == 7
