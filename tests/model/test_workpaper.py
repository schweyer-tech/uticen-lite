"""Tests for Workpaper assembly and serialisation."""

from __future__ import annotations

import pathlib

import pytest

from controlflow_sdk.model.control import ControlDef, FrameworkRefs
from controlflow_sdk.model.run import RunRecord
from controlflow_sdk.model.violation import Severity, Violation
from controlflow_sdk.model.workpaper import Workpaper

GENERATED_AT = "2026-06-16T00:00:00Z"
TEST_PY_CONTENT = """\
# Sample control test
def run(df):
    return df[df["amount"] > 1000]
"""


@pytest.fixture()
def test_py_file(tmp_path: pathlib.Path) -> pathlib.Path:
    """Write a small test.py and return its absolute path."""
    p = tmp_path / "test.py"
    p.write_text(TEST_PY_CONTENT, encoding="utf-8")
    return p


@pytest.fixture()
def control(test_py_file: pathlib.Path) -> ControlDef:
    return ControlDef(
        id="ctrl-001",
        title="Invoice Amount Control",
        objective="Ensure no invoices exceed approved limits.",
        narrative="All invoices over $1,000 require dual approval.",
        framework_refs=FrameworkRefs(nist=["AC-2", "AU-6"], extra={"ISO": ["A.12.1"]}),
        risk=None,
        sources=[],
        test_path=str(test_py_file),
    )


@pytest.fixture()
def run_record() -> RunRecord:
    return RunRecord(
        control_id="ctrl-001",
        executed_at="2026-06-16T00:00:00Z",
        population_size=50,
        violations=[
            Violation(
                item_key="INV-999",
                description="Amount exceeds limit",
                severity=Severity.HIGH,
            )
        ],
    )


# ── assemble ──────────────────────────────────────────────────────────────────


class TestWorkpaperAssemble:
    def test_returns_workpaper_instance(self, control: ControlDef, run_record: RunRecord) -> None:
        wp = Workpaper.assemble(control, run_record, GENERATED_AT)
        assert isinstance(wp, Workpaper)

    def test_scalar_fields_copied_from_control(
        self, control: ControlDef, run_record: RunRecord
    ) -> None:
        wp = Workpaper.assemble(control, run_record, GENERATED_AT)
        assert wp.control_id == control.id
        assert wp.title == control.title
        assert wp.objective == control.objective
        assert wp.narrative == control.narrative
        assert wp.generated_at == GENERATED_AT

    def test_framework_refs_serialised_to_dict(
        self, control: ControlDef, run_record: RunRecord
    ) -> None:
        wp = Workpaper.assemble(control, run_record, GENERATED_AT)
        assert isinstance(wp.framework_refs, dict)
        assert wp.framework_refs["nist"] == ["AC-2", "AU-6"]
        assert wp.framework_refs["extra"] == {"ISO": ["A.12.1"]}

    def test_one_procedure_created(self, control: ControlDef, run_record: RunRecord) -> None:
        wp = Workpaper.assemble(control, run_record, GENERATED_AT)
        assert len(wp.procedures) == 1

    def test_procedure_result_is_same_run_object(
        self, control: ControlDef, run_record: RunRecord
    ) -> None:
        wp = Workpaper.assemble(control, run_record, GENERATED_AT)
        assert wp.procedures[0].result is run_record

    def test_procedure_test_code_matches_file_contents(
        self, control: ControlDef, run_record: RunRecord
    ) -> None:
        wp = Workpaper.assemble(control, run_record, GENERATED_AT)
        assert wp.procedures[0].test_code == TEST_PY_CONTENT

    def test_procedure_title_and_narrative_set(
        self, control: ControlDef, run_record: RunRecord
    ) -> None:
        wp = Workpaper.assemble(control, run_record, GENERATED_AT)
        proc = wp.procedures[0]
        assert proc.title  # non-empty
        assert proc.narrative  # non-empty


# ── to_dict ───────────────────────────────────────────────────────────────────


class TestWorkpaperToDict:
    def test_top_level_keys_present(self, control: ControlDef, run_record: RunRecord) -> None:
        d = Workpaper.assemble(control, run_record, GENERATED_AT).to_dict()
        for key in (
            "control_id",
            "title",
            "objective",
            "narrative",
            "framework_refs",
            "procedures",
            "generated_at",
        ):
            assert key in d, f"missing key: {key}"

    def test_procedures_list_length(self, control: ControlDef, run_record: RunRecord) -> None:
        d = Workpaper.assemble(control, run_record, GENERATED_AT).to_dict()
        assert len(d["procedures"]) == 1

    def test_procedure_result_failed_count(
        self, control: ControlDef, run_record: RunRecord
    ) -> None:
        d = Workpaper.assemble(control, run_record, GENERATED_AT).to_dict()
        assert d["procedures"][0]["result"]["failed"] == 1

    def test_procedure_test_code_in_dict(self, control: ControlDef, run_record: RunRecord) -> None:
        d = Workpaper.assemble(control, run_record, GENERATED_AT).to_dict()
        assert d["procedures"][0]["test_code"] == TEST_PY_CONTENT

    def test_procedure_result_dict_shape(self, control: ControlDef, run_record: RunRecord) -> None:
        d = Workpaper.assemble(control, run_record, GENERATED_AT).to_dict()
        result = d["procedures"][0]["result"]
        assert result["passed"] == 49
        assert result["total"] == 50
        assert "pass_rate" in result
        assert "violations" in result["details"]
