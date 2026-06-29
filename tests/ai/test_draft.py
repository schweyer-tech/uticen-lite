"""Unit tests for the provider-agnostic draft + validation gate.

Every test drives a *fake* ``Provider`` so the suite never makes a network call
and passes with the ``[ai]`` SDKs absent. The validation gate (parse_rule_spec +
run-on-sample + column check) is identical for all three real backends.
"""

from __future__ import annotations

import json

import pytest

from uticen_lite import ai
from uticen_lite.ai.draft import RULE_SPEC_JSON_SCHEMA, DraftError, draft_and_validate
from uticen_lite.rules.spec import OPERATORS, RuleSpecError, parse_rule_spec

# A 2-row sample (≥2 rows per learning 0004): one row trips the rule, one doesn't.
_SCHEMA = [
    {"original_name": "amount", "display_name": "Amount", "data_type": "number"},
    {"original_name": "approved_by", "display_name": "Approved By", "data_type": "text"},
]
_SAMPLE = {
    "columns": ["amount", "approved_by"],
    "schema": _SCHEMA,
    "rows": [
        ["1000", "alice"],
        ["50", ""],
    ],
}


def _fake_provider(spec: dict):
    """A Provider that always returns *spec* (monkeypatched into get_provider)."""

    class _Fake:
        def draft_rule_spec(self, objective, source_schema, data_sample, *, model):
            return spec

    return _Fake()


def _patch_provider(monkeypatch, spec: dict) -> None:
    monkeypatch.setattr(
        "uticen_lite.ai.draft.get_provider", lambda provider: _fake_provider(spec)
    )


def test_valid_draft_roundtrips_and_runs(monkeypatch):
    good = {
        "logic": "all",
        "severity": "high",
        "description_template": "{approved_by} approved a large amount",
        "conditions": [
            {"column": "amount", "op": "gt", "value": 100},
            {"column": "approved_by", "op": "is_empty"},
        ],
    }
    _patch_provider(monkeypatch, good)
    out = draft_and_validate(
        objective="Flag large unapproved payments",
        source_schema={"columns": _SCHEMA},
        data_sample=_SAMPLE,
        provider="anthropic",
        model="claude-opus-4-8",
    )
    # The validated dict round-trips through our own parser.
    assert out == good
    spec = parse_rule_spec(out)
    assert spec.logic == "all"
    assert [c.op for c in spec.conditions] == ["gt", "is_empty"]


def test_spec_that_flags_a_sample_row_runs(monkeypatch):
    # amount > 100 trips exactly the first row of the 2-row sample.
    spec = {"logic": "all", "conditions": [{"column": "amount", "op": "gt", "value": 100}]}
    _patch_provider(monkeypatch, spec)
    # Should not raise — the gate proves it executes on the sample.
    out = draft_and_validate(
        objective="o", source_schema={"columns": _SCHEMA}, data_sample=_SAMPLE,
        provider="anthropic", model="claude-opus-4-8",
    )
    assert out == spec


def test_bad_op_surfaces_rulespecerror(monkeypatch):
    bad = {"logic": "all", "conditions": [{"column": "amount", "op": "bogus_op", "value": 1}]}
    _patch_provider(monkeypatch, bad)
    with pytest.raises(RuleSpecError):
        draft_and_validate(
            objective="o", source_schema={"columns": _SCHEMA}, data_sample=_SAMPLE,
            provider="anthropic", model="claude-opus-4-8",
        )


def test_hallucinated_column_raises_drafterror(monkeypatch):
    bad = {"logic": "all", "conditions": [{"column": "ghost_col", "op": "not_empty"}]}
    _patch_provider(monkeypatch, bad)
    with pytest.raises(DraftError) as exc:
        draft_and_validate(
            objective="o", source_schema={"columns": _SCHEMA}, data_sample=_SAMPLE,
            provider="anthropic", model="claude-opus-4-8",
        )
    assert "ghost_col" in str(exc.value)


def test_spec_that_raises_in_evaluate_raises_drafterror(monkeypatch):
    # A malformed regex raises at evaluate time (re.error) — must be wrapped.
    bad = {"logic": "all", "conditions": [{"column": "approved_by", "op": "regex", "value": "("}]}
    _patch_provider(monkeypatch, bad)
    with pytest.raises(DraftError):
        draft_and_validate(
            objective="o", source_schema={"columns": _SCHEMA}, data_sample=_SAMPLE,
            provider="anthropic", model="claude-opus-4-8",
        )


def test_rule_spec_json_schema_is_well_formed():
    # Serializable + valid JSON-Schema with additionalProperties:false at both levels.
    json.dumps(RULE_SPEC_JSON_SCHEMA)
    import jsonschema

    jsonschema.Draft202012Validator.check_schema(RULE_SPEC_JSON_SCHEMA)
    assert RULE_SPEC_JSON_SCHEMA["additionalProperties"] is False
    cond = RULE_SPEC_JSON_SCHEMA["properties"]["conditions"]["items"]
    assert cond["additionalProperties"] is False


def test_op_enum_equals_operators_no_drift():
    # The schema's op enum must track rules.spec.OPERATORS exactly — a guard so
    # adding an engine operator can't silently desync the AI schema.
    cond = RULE_SPEC_JSON_SCHEMA["properties"]["conditions"]["items"]
    assert cond["properties"]["op"]["enum"] == sorted(OPERATORS)


def test_public_surface_importable():
    # The package surface is import-safe without the [ai] SDKs.
    assert hasattr(ai, "draft_and_validate")
    assert hasattr(ai, "available_providers")
    assert hasattr(ai, "provider_key_present")
    assert hasattr(ai, "RULE_SPEC_JSON_SCHEMA")
    assert hasattr(ai, "DraftError")
