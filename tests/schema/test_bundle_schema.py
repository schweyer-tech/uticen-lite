"""TDD tests for the full bundle.schema.json contract (Phase 3, Task 1)."""

from __future__ import annotations

import importlib.resources
import json

from uticen_lite.schema.validate import validate_bundle

# ---------------------------------------------------------------------------
# Minimal valid bundle fixture
# ---------------------------------------------------------------------------
# Built to match the REAL to_dict() output shapes:
#   - ControlDef.to_dict() for the control fields
#   - SourceBinding.to_data_source() (+ "id") for each source
#   - RunRecord.to_dict() for run records
#   - Workpaper.to_dict() / Procedure.to_dict() for the workpaper
# ---------------------------------------------------------------------------

_VALID_RUN = {
    # RunRecord.to_dict() fields
    "run_id": "abc123def456abcd",
    "executed_at": "2024-01-15T12:00:00Z",
    "passed": 98,
    "failed": 2,
    "total": 100,
    "pass_rate": 98.0,
    "summary": "2 violation(s) across 100 record(s)",
    "details": {
        "violations": [
            {
                "item_key": "INV-001",
                "description": "Posted after cutoff",
                "severity": "medium",
                "details": {},
            }
        ]
    },
    "control_id": "cash-cutoff-001",
    "provenance": [
        {
            "source_id": "gl-2024",
            "path": "data/gl.csv",
            "sha256": "deadbeef" * 8,
            "row_count": 100,
        }
    ],
}

_VALID_WORKPAPER = {
    "control_id": "cash-cutoff-001",
    "title": "Cash Cutoff Control",
    "objective": "Ensure all transactions are recorded in the correct period.",
    "narrative": "We review GL entries around period end for cutoff violations.",
    "framework_refs": {"nist": ["AC-2"], "extra": {}},
    "procedures": [
        {
            "title": "Cash Cutoff Control",
            "narrative": "We review GL entries around period end for cutoff violations.",
            "test_code": "result = {'violations': []}",
            "result": _VALID_RUN,
        }
    ],
    "generated_at": "2024-01-15T12:00:00Z",
}

_VALID_SOURCE = {
    # id is added at bundle level (not in to_data_source() but needed by importer)
    "id": "gl-2024",
    "type": "file",
    "key_config": {"mode": "auto"},
    "column_mappings": [
        {"original_name": "entry_date", "display_name": "Entry Date"},
        {"original_name": "amount", "display_name": "Amount"},
    ],
}

_VALID_CONTROL = {
    "id": "cash-cutoff-001",
    "title": "Cash Cutoff Control",
    "objective": "Ensure all transactions are recorded in the correct period.",
    "narrative": "We review GL entries around period end for cutoff violations.",
    "framework_refs": {"nist": ["AC-2"], "extra": {}},
    "risk": {
        "name": "Cutoff Risk",
        "description": "Transactions may be recorded in the wrong period.",
        "inherent_rating": "high",
    },
    "sources": [_VALID_SOURCE],
    "test_code": "result = {'violations': []}",
    "workpaper": _VALID_WORKPAPER,
    "runs": [_VALID_RUN],
}

_VALID_BUNDLE = {
    "schema_version": "1.0",
    "project": {
        "name": "My Audit Project",
        "framework": "NIST 800-53",
        "system": "General Ledger",
    },
    "controls": [_VALID_CONTROL],
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_valid_bundle_passes():
    """A minimal but complete bundle document passes validate_bundle."""
    errors = validate_bundle(_VALID_BUNDLE)
    assert errors == [], f"Expected no errors, got: {errors}"


def test_missing_schema_version_reports_error():
    """Dropping schema_version yields an error mentioning 'schema_version'."""
    doc = {k: v for k, v in _VALID_BUNDLE.items() if k != "schema_version"}
    errors = validate_bundle(doc)
    assert errors, "Expected validation errors, got none"
    assert any("schema_version" in e for e in errors), (
        f"Expected 'schema_version' in errors, got: {errors}"
    )


def test_wrong_schema_version_reports_error():
    """A schema_version that doesn't match the const yields an error."""
    doc = {**_VALID_BUNDLE, "schema_version": "2.0"}
    errors = validate_bundle(doc)
    assert errors, "Expected validation errors for wrong schema_version"


def test_control_missing_test_code_reports_error():
    """A control missing test_code yields an error."""
    bad_control = {k: v for k, v in _VALID_CONTROL.items() if k != "test_code"}
    doc = {**_VALID_BUNDLE, "controls": [bad_control]}
    errors = validate_bundle(doc)
    assert errors, "Expected validation errors, got none"
    assert any("test_code" in e for e in errors), f"Expected 'test_code' in errors, got: {errors}"


def test_source_missing_key_config_reports_error():
    """A sources[] entry missing key_config yields an error."""
    bad_source = {k: v for k, v in _VALID_SOURCE.items() if k != "key_config"}
    bad_control = {**_VALID_CONTROL, "sources": [bad_source]}
    doc = {**_VALID_BUNDLE, "controls": [bad_control]}
    errors = validate_bundle(doc)
    assert errors, "Expected validation errors, got none"
    assert any("key_config" in e for e in errors), f"Expected 'key_config' in errors, got: {errors}"


def test_risk_is_optional():
    """risk is optional — a control with risk=null passes."""
    control_no_risk = {**_VALID_CONTROL, "risk": None}
    doc = {**_VALID_BUNDLE, "controls": [control_no_risk]}
    errors = validate_bundle(doc)
    assert errors == [], f"Expected no errors with null risk, got: {errors}"


def test_project_name_is_required():
    """project.name is required."""
    bad_project = {"framework": "NIST 800-53"}
    doc = {**_VALID_BUNDLE, "project": bad_project}
    errors = validate_bundle(doc)
    assert errors, "Expected validation errors for missing project.name"
    assert any("name" in e for e in errors), f"Expected 'name' in errors, got: {errors}"


def test_procedure_with_code_and_assertion_validates():
    """A procedure carrying code/assertion passes — they are optional, not required."""
    proc_with_extras = {
        "title": "Manual JE Review",
        "narrative": "we tested…",
        "test_code": "def test(pop): ...",
        "result": _VALID_RUN,
        "code": "P1",
        "assertion": "Segregation of Duties",
    }
    workpaper = {**_VALID_WORKPAPER, "procedures": [proc_with_extras]}
    doc = {**_VALID_BUNDLE, "controls": [{**_VALID_CONTROL, "workpaper": workpaper}]}
    errors = validate_bundle(doc)
    assert errors == [], f"Procedure with code/assertion should validate, got: {errors}"


def test_procedure_required_fields_unchanged():
    """procedure.required stays [title, narrative, test_code, result]; code/assertion optional."""
    schema_bytes = (
        importlib.resources.files("uticen_lite.schema")
        .joinpath("bundle.schema.json")
        .read_bytes()
    )
    schema = json.loads(schema_bytes)
    proc_required = schema["$defs"]["procedure"]["required"]
    assert set(proc_required) == {"title", "narrative", "test_code", "result"}, (
        f"procedure required changed unexpectedly: {proc_required}"
    )
    # code and assertion must NOT be in required
    assert "code" not in proc_required
    assert "assertion" not in proc_required
    # but they should be in properties (optional)
    proc_props = schema["$defs"]["procedure"].get("properties", {})
    assert "code" in proc_props, "code missing from procedure.properties"
    assert "assertion" in proc_props, "assertion missing from procedure.properties"
