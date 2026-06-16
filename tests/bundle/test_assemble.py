"""TDD tests for controlflow_sdk.bundle.assemble (Phase 3, Task 2).

Red → Green cycle:
  1. Write tests (RED – bundle module does not exist yet).
  2. Implement assemble.py.
  3. Tests turn GREEN.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from controlflow_sdk.model.control import ControlDef, FrameworkRefs, RiskRef, SourceBinding
from controlflow_sdk.project.discovery import Project
from controlflow_sdk.project.loader import ProjectConfig

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

GENERATED_AT = "2026-06-16T00:00:00Z"

TEST_PY_CONTENT = """\
# Cash cutoff test
def test(pop):
    return []
"""

_SOURCE_BINDING = SourceBinding(
    id="gl",
    type="file",
    config={"path": "gl.csv", "format": "csv"},
    key_config={"mode": "single", "columns": ["entry_id"]},
    column_mappings=[
        {
            "original_name": "entry_id",
            "display_name": "Entry ID",
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

_VALID_RUN: dict[str, Any] = {
    "run_id": "abc123def456abcd",
    "executed_at": "2026-06-16T00:00:00Z",
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
    "control_id": "cash_cutoff",
    "provenance": [
        {
            "source_id": "gl",
            "path": "gl.csv",
            "sha256": "deadbeef" * 8,
            "row_count": 100,
        }
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_py_file(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "test.py"
    p.write_text(TEST_PY_CONTENT, encoding="utf-8")
    return p


@pytest.fixture()
def control(test_py_file: pathlib.Path) -> ControlDef:
    return ControlDef(
        id="cash_cutoff",
        title="Cash Cutoff Control",
        objective="Ensure cash transactions are recorded in the correct period.",
        narrative="All cash receipts are reviewed at period end.",
        framework_refs=FrameworkRefs(nist=["AC-2"], extra={}),
        risk=RiskRef(
            name="Cutoff Risk", description="Wrong period recording", inherent_rating="high"
        ),
        sources=[_SOURCE_BINDING],
        test_path=str(test_py_file),
    )


@pytest.fixture()
def project(control: ControlDef) -> Project:
    config = ProjectConfig(
        name="Sample Audit Project",
        framework="NIST SP 800-53",
        system={"name": "General Ledger System"},
        defaults={},
    )
    return Project(
        config=config,
        sources={"gl": _SOURCE_BINDING},
        controls=[control],
    )


@pytest.fixture()
def runs_by_control(control: ControlDef) -> dict[str, list[dict[str, Any]]]:
    return {control.id: [_VALID_RUN]}


# ---------------------------------------------------------------------------
# Helper: recursive key walker for trust-boundary guard
# ---------------------------------------------------------------------------


def _all_keys(obj: Any) -> set[str]:
    """Recursively collect every dict key in obj (including nested dicts/lists)."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= _all_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            keys |= _all_keys(item)
    return keys


# ---------------------------------------------------------------------------
# Import the module under test (will fail RED until implemented)
# ---------------------------------------------------------------------------


from controlflow_sdk.bundle import BundleError, assemble_bundle  # noqa: E402

# ---------------------------------------------------------------------------
# Tests: happy path
# ---------------------------------------------------------------------------


class TestAssembleBundleHappyPath:
    def test_returns_dict(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert isinstance(result, dict)

    def test_schema_version_present(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert result["schema_version"] == "1.0"

    def test_project_block_name(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert result["project"]["name"] == "Sample Audit Project"

    def test_project_block_framework(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert result["project"]["framework"] == "NIST SP 800-53"

    def test_project_block_system_is_string(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        """system is serialised from a dict to a string (its 'name' value or repr)."""
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        # Bundle schema expects project.system to be a string
        assert isinstance(result["project"]["system"], str)

    def test_controls_array_length(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert len(result["controls"]) == 1

    def test_control_id(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert result["controls"][0]["id"] == "cash_cutoff"

    def test_control_title(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert result["controls"][0]["title"] == "Cash Cutoff Control"

    def test_control_test_code_matches_file(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert result["controls"][0]["test_code"] == TEST_PY_CONTENT

    def test_control_sources_include_id(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        """Each source dict must include 'id' alongside type/key_config/column_mappings."""
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        sources = result["controls"][0]["sources"]
        assert len(sources) == 1
        assert sources[0]["id"] == "gl"

    def test_control_sources_have_type(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert result["controls"][0]["sources"][0]["type"] == "file"

    def test_control_sources_have_key_config(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert result["controls"][0]["sources"][0]["key_config"]["mode"] == "single"

    def test_control_sources_have_column_mappings(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        cms = result["controls"][0]["sources"][0]["column_mappings"]
        assert len(cms) == 2

    def test_control_runs_present(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert len(result["controls"][0]["runs"]) == 1

    def test_control_run_has_pass_rate(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert result["controls"][0]["runs"][0]["pass_rate"] == 98.0

    def test_control_workpaper_present(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        wp = result["controls"][0]["workpaper"]
        assert wp["control_id"] == "cash_cutoff"
        assert "procedures" in wp

    def test_control_risk_object(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        risk = result["controls"][0]["risk"]
        assert risk is not None
        assert risk["name"] == "Cutoff Risk"

    def test_passes_validate_bundle(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        """The assembled manifest must pass the JSON schema validator."""
        from controlflow_sdk.schema.validate import validate_bundle

        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        errors = validate_bundle(result)
        assert errors == [], f"Bundle failed schema validation: {errors}"

    def test_output_keys_are_sorted(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        """Top-level dict keys must be in sorted order for determinism."""
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        keys = list(result.keys())
        assert keys == sorted(keys), f"Keys not sorted: {keys}"

    def test_framework_refs_serialised(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        fr = result["controls"][0]["framework_refs"]
        assert fr["nist"] == ["AC-2"]


# ---------------------------------------------------------------------------
# Tests: trust-boundary guard — NO raw population data in the manifest
# ---------------------------------------------------------------------------


class TestTrustBoundaryGuard:
    """Manifest must never contain raw population data or filesystem paths."""

    FORBIDDEN_KEYS = {"rows", "data", "data_rows", "test_path"}

    def test_no_forbidden_keys_anywhere(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        """Recursively walk the manifest — none of the forbidden keys may appear."""
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        found = _all_keys(result) & self.FORBIDDEN_KEYS
        assert not found, (
            f"Forbidden key(s) found in manifest: {sorted(found)}. "
            "The bundle must not contain raw population data or local filesystem paths."
        )

    def test_no_rows_key(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert "rows" not in _all_keys(result)

    def test_no_data_key(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert "data" not in _all_keys(result)

    def test_no_data_rows_key(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert "data_rows" not in _all_keys(result)

    def test_no_test_path_key(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        """test_path (absolute local filesystem path) must NOT appear in the manifest."""
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert "test_path" not in _all_keys(result)

    def test_test_code_content_is_present(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        """We embed the file CONTENT (test_code), not the path (test_path)."""
        result = assemble_bundle(project, runs_by_control, GENERATED_AT)
        assert "test_code" in _all_keys(result)


# ---------------------------------------------------------------------------
# Tests: control with no runs
# ---------------------------------------------------------------------------


class TestControlWithNoRuns:
    def test_runs_is_empty_list(self, project: Project) -> None:
        result = assemble_bundle(project, {}, GENERATED_AT)
        assert result["controls"][0]["runs"] == []

    def test_workpaper_has_no_procedures_when_no_runs(self, project: Project) -> None:
        result = assemble_bundle(project, {}, GENERATED_AT)
        wp = result["controls"][0]["workpaper"]
        assert wp["procedures"] == []

    def test_still_passes_schema_when_no_runs(self, project: Project) -> None:
        from controlflow_sdk.schema.validate import validate_bundle

        result = assemble_bundle(project, {}, GENERATED_AT)
        errors = validate_bundle(result)
        assert errors == [], f"Bundle failed schema validation (no runs): {errors}"


# ---------------------------------------------------------------------------
# Tests: control with null risk
# ---------------------------------------------------------------------------


class TestControlWithNullRisk:
    def test_risk_is_null(self, test_py_file: pathlib.Path) -> None:
        control_no_risk = ControlDef(
            id="ctrl_no_risk",
            title="No Risk Control",
            objective="Objective.",
            narrative="Narrative.",
            framework_refs=FrameworkRefs(nist=[], extra={}),
            risk=None,
            sources=[_SOURCE_BINDING],
            test_path=str(test_py_file),
        )
        config = ProjectConfig(name="Proj", framework=None, system={}, defaults={})
        proj = Project(config=config, sources={"gl": _SOURCE_BINDING}, controls=[control_no_risk])
        result = assemble_bundle(proj, {}, GENERATED_AT)
        assert result["controls"][0]["risk"] is None

    def test_passes_schema_with_null_risk(self, test_py_file: pathlib.Path) -> None:
        from controlflow_sdk.schema.validate import validate_bundle

        control_no_risk = ControlDef(
            id="ctrl_no_risk",
            title="No Risk Control",
            objective="Objective.",
            narrative="Narrative.",
            framework_refs=FrameworkRefs(nist=[], extra={}),
            risk=None,
            sources=[_SOURCE_BINDING],
            test_path=str(test_py_file),
        )
        config = ProjectConfig(name="Proj", framework=None, system={}, defaults={})
        proj = Project(config=config, sources={"gl": _SOURCE_BINDING}, controls=[control_no_risk])
        result = assemble_bundle(proj, {}, GENERATED_AT)
        errors = validate_bundle(result)
        assert errors == [], f"Bundle failed schema validation (null risk): {errors}"


# ---------------------------------------------------------------------------
# Tests: BundleError raised on schema violation
# ---------------------------------------------------------------------------


class TestBundleError:
    def test_bundle_error_is_exception(self) -> None:
        assert issubclass(BundleError, Exception)

    def test_bundle_error_raised_when_schema_invalid(
        self, project: Project, runs_by_control: dict[str, list[dict[str, Any]]]
    ) -> None:
        """Monkey-patch validate_bundle to always return errors → BundleError raised."""
        import controlflow_sdk.bundle.assemble as assemble_mod

        original = assemble_mod.validate_bundle

        def always_invalid(doc: dict[str, Any]) -> list[str]:
            return ["<root>: forced validation failure"]

        assemble_mod.validate_bundle = always_invalid  # type: ignore[assignment]
        try:
            with pytest.raises(BundleError) as exc_info:
                assemble_bundle(project, runs_by_control, GENERATED_AT)
            assert "forced validation failure" in str(exc_info.value)
        finally:
            assemble_mod.validate_bundle = original  # type: ignore[assignment]
