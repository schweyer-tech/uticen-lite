"""Tests for ControlDef, SourceBinding, FrameworkRefs, and RiskRef models."""

from __future__ import annotations

from controlflow_sdk.model.control import (
    ControlDef,
    FrameworkRefs,
    RiskRef,
    SourceBinding,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_source_binding() -> SourceBinding:
    return SourceBinding(
        id="ds-gl",
        type="file",
        config={"path": "gl.csv", "format": "csv"},
        key_config={"mode": "single", "columns": ["invoice_id"]},
        column_mappings=[
            {
                "original_name": "invoice_id",
                "display_name": "Invoice ID",
                "is_key": True,
                "include": True,
            },
            {
                "original_name": "amount",
                "display_name": "Amount",
                "is_key": False,
                "include": True,
            },
        ],
    )


def make_control_def() -> ControlDef:
    return ControlDef(
        id="ctrl-001",
        title="Three-Way Match",
        objective="Verify PO/GR/Invoice amounts agree.",
        narrative=(
            "Each invoice is matched against the PO and goods-receipt to detect over-billing."
        ),
        framework_refs=FrameworkRefs(
            nist=["AC-3", "AU-2"],
            extra={"iso27001": ["A.12.1"]},
        ),
        risk=RiskRef(
            name="Vendor over-billing",
            description="Vendor charges more than contracted amount.",
            inherent_rating="high",
        ),
        sources=[make_source_binding()],
        severity_policy={"default": "medium", "overrides": {}},
        test_path="controls/three_way_match/test.py",
    )


# ---------------------------------------------------------------------------
# FrameworkRefs tests
# ---------------------------------------------------------------------------


class TestFrameworkRefs:
    def test_defaults(self) -> None:
        refs = FrameworkRefs()
        assert refs.nist == []
        assert refs.extra == {}

    def test_populated(self) -> None:
        refs = FrameworkRefs(nist=["AC-3"], extra={"iso": ["A.1"]})
        assert refs.nist == ["AC-3"]
        assert refs.extra == {"iso": ["A.1"]}

    def test_mutable_defaults_are_independent(self) -> None:
        """Two instances must not share the same list/dict object."""
        a = FrameworkRefs()
        b = FrameworkRefs()
        a.nist.append("AC-3")
        assert b.nist == [], "FrameworkRefs instances share a mutable default"


# ---------------------------------------------------------------------------
# RiskRef tests
# ---------------------------------------------------------------------------


class TestRiskRef:
    def test_required_field(self) -> None:
        ref = RiskRef(name="Fraud")
        assert ref.name == "Fraud"

    def test_optional_defaults(self) -> None:
        ref = RiskRef(name="Fraud")
        assert ref.description == ""
        assert ref.inherent_rating is None

    def test_fully_populated(self) -> None:
        ref = RiskRef(name="Fraud", description="Desc", inherent_rating="critical")
        assert ref.inherent_rating == "critical"


# ---------------------------------------------------------------------------
# SourceBinding tests
# ---------------------------------------------------------------------------


class TestSourceBinding:
    def test_to_data_source_exact_keys(self) -> None:
        """to_data_source() must return exactly {type, key_config, column_mappings}."""
        sb = make_source_binding()
        result = sb.to_data_source()
        assert set(result.keys()) == {"type", "key_config", "column_mappings"}, (
            f"Expected exactly {{type, key_config, column_mappings}}, got {set(result.keys())}"
        )

    def test_to_data_source_excludes_id_and_config(self) -> None:
        sb = make_source_binding()
        result = sb.to_data_source()
        assert "id" not in result
        assert "config" not in result

    def test_to_data_source_type_value(self) -> None:
        sb = make_source_binding()
        assert sb.to_data_source()["type"] == "file"

    def test_to_data_source_key_config(self) -> None:
        sb = make_source_binding()
        assert sb.to_data_source()["key_config"] == {"mode": "single", "columns": ["invoice_id"]}

    def test_to_data_source_column_mappings(self) -> None:
        sb = make_source_binding()
        cms = sb.to_data_source()["column_mappings"]
        assert isinstance(cms, list)
        assert len(cms) == 2
        first = cms[0]
        assert first["original_name"] == "invoice_id"
        assert first["is_key"] is True

    def test_to_data_source_returns_new_dict(self) -> None:
        """Mutating the returned dict must not affect the binding's internal state."""
        sb = make_source_binding()
        d = sb.to_data_source()
        d["extra_key"] = "injected"
        assert "extra_key" not in sb.to_data_source()

    def test_to_data_source_column_mappings_is_copy(self) -> None:
        """The column_mappings list in the returned dict is independent."""
        sb = make_source_binding()
        d = sb.to_data_source()
        d["column_mappings"].append({"original_name": "phantom"})
        assert len(sb.to_data_source()["column_mappings"]) == 2


# ---------------------------------------------------------------------------
# ControlDef tests
# ---------------------------------------------------------------------------


class TestControlDef:
    def test_to_dict_id(self) -> None:
        ctrl = make_control_def()
        assert ctrl.to_dict()["id"] == "ctrl-001"

    def test_to_dict_title(self) -> None:
        ctrl = make_control_def()
        assert ctrl.to_dict()["title"] == "Three-Way Match"

    def test_to_dict_objective(self) -> None:
        ctrl = make_control_def()
        assert ctrl.to_dict()["objective"] == "Verify PO/GR/Invoice amounts agree."

    def test_to_dict_narrative(self) -> None:
        ctrl = make_control_def()
        assert ctrl.to_dict()["narrative"] == (
            "Each invoice is matched against the PO and goods-receipt to detect over-billing."
        )

    def test_to_dict_framework_refs_nist(self) -> None:
        ctrl = make_control_def()
        refs = ctrl.to_dict()["framework_refs"]
        assert refs["nist"] == ["AC-3", "AU-2"]

    def test_to_dict_framework_refs_extra(self) -> None:
        ctrl = make_control_def()
        refs = ctrl.to_dict()["framework_refs"]
        assert refs["extra"] == {"iso27001": ["A.12.1"]}

    def test_to_dict_risk(self) -> None:
        ctrl = make_control_def()
        risk = ctrl.to_dict()["risk"]
        assert risk["name"] == "Vendor over-billing"
        assert risk["description"] == "Vendor charges more than contracted amount."
        assert risk["inherent_rating"] == "high"

    def test_to_dict_risk_none(self) -> None:
        ctrl = make_control_def()
        ctrl_no_risk = ControlDef(
            id=ctrl.id,
            title=ctrl.title,
            objective=ctrl.objective,
            narrative=ctrl.narrative,
            framework_refs=ctrl.framework_refs,
            risk=None,
            sources=ctrl.sources,
            test_path=ctrl.test_path,
        )
        assert ctrl_no_risk.to_dict()["risk"] is None

    def test_to_dict_sources_count(self) -> None:
        ctrl = make_control_def()
        assert len(ctrl.to_dict()["sources"]) == 1

    def test_to_dict_sources_shape(self) -> None:
        """Each source entry in to_dict() is the app data_sources shape."""
        ctrl = make_control_def()
        src = ctrl.to_dict()["sources"][0]
        assert set(src.keys()) == {"type", "key_config", "column_mappings"}

    def test_to_dict_severity_policy(self) -> None:
        ctrl = make_control_def()
        assert ctrl.to_dict()["severity_policy"] == {"default": "medium", "overrides": {}}

    def test_to_dict_test_path(self) -> None:
        ctrl = make_control_def()
        assert ctrl.to_dict()["test_path"] == "controls/three_way_match/test.py"

    def test_to_dict_key_set(self) -> None:
        ctrl = make_control_def()
        keys = set(ctrl.to_dict().keys())
        expected = {
            "id",
            "title",
            "objective",
            "narrative",
            "framework_refs",
            "risk",
            "sources",
            "severity_policy",
            "test_path",
        }
        assert keys == expected, f"Unexpected keys: {keys ^ expected}"

    def test_severity_policy_mutable_default_independent(self) -> None:
        """Two ControlDef instances with default severity_policy don't share state."""
        c1 = ControlDef(
            id="a",
            title="A",
            objective="o",
            narrative="n",
            framework_refs=FrameworkRefs(),
            risk=None,
            sources=[],
            test_path="t.py",
        )
        c2 = ControlDef(
            id="b",
            title="B",
            objective="o",
            narrative="n",
            framework_refs=FrameworkRefs(),
            risk=None,
            sources=[],
            test_path="t.py",
        )
        c1.severity_policy["injected"] = True
        assert "injected" not in c2.severity_policy
