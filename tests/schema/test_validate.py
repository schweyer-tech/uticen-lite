"""Tests for JSON-Schema contracts + validator (Task 5)."""

from uticen_lite.schema.validate import validate_control, validate_sources


def test_valid_control_passes():
    doc = {
        "id": "cash-cutoff",
        "title": "Cash cutoff",
        "objective": "x",
        "narrative": "y",
        "framework_refs": {"nist": ["AC-2"]},
        "sources": [{"id": "gl"}],
    }
    assert validate_control(doc) == []


def test_control_missing_id_reports_error():
    errs = validate_control({"title": "t"})
    assert any("id" in e for e in errs)


def test_sources_requires_key_config():
    errs = validate_sources(
        {"sources": [{"id": "gl", "type": "file", "config": {"path": "g.csv"}}]}
    )
    assert any("key_config" in e for e in errs)
