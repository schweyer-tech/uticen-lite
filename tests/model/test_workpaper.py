"""Tests for Workpaper assembly and serialisation."""

from __future__ import annotations

import pathlib

import pytest

from uticen_lite.model.control import ControlDef, FrameworkRefs, Threshold
from uticen_lite.model.run import RunRecord
from uticen_lite.model.violation import Severity, Violation
from uticen_lite.model.workpaper import Procedure, ProcedureSpec, Workpaper

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


# ── per-procedure threshold + any-fails roll-up ───────────────────────────────


def _v() -> Violation:
    """Build a minimal Violation for use in test fixtures."""
    return Violation(item_key="k1", description="desc")


class TestProcedureDetermination:
    def test_procedure_determination_passed(self) -> None:
        """A procedure with 0 violations and implicit-zero threshold passes."""
        proc = Procedure(
            title="A",
            narrative="",
            test_code="...",
            result=RunRecord(
                control_id="C",
                executed_at="2026-01-01T00:00:00Z",
                population_size=100,
                violations=[],
            ),
            threshold=Threshold(),
        )
        assert proc.determination.passed is True

    def test_procedure_determination_failed(self) -> None:
        """A procedure with violations and implicit-zero threshold fails."""
        proc = Procedure(
            title="B",
            narrative="",
            test_code="...",
            result=RunRecord(
                control_id="C",
                executed_at="2026-01-01T00:00:00Z",
                population_size=50,
                violations=[_v(), _v(), _v()],
            ),
            threshold=Threshold(),
        )
        assert proc.determination.passed is False

    def test_procedure_determination_uses_own_threshold(self) -> None:
        """A procedure with 5% threshold passes 3/150 (2%) but fails 3/50 (6%)."""
        threshold_5pct = Threshold(failure_threshold_pct=5.0)
        # 3/150 = 2% → passes
        proc_pass = Procedure(
            title="A",
            narrative="",
            test_code="...",
            result=RunRecord(
                control_id="C",
                executed_at="2026-01-01T00:00:00Z",
                population_size=150,
                violations=[_v(), _v(), _v()],
            ),
            threshold=threshold_5pct,
        )
        assert proc_pass.determination.passed is True
        # 3/50 = 6% → fails
        proc_fail = Procedure(
            title="B",
            narrative="",
            test_code="...",
            result=RunRecord(
                control_id="C",
                executed_at="2026-01-01T00:00:00Z",
                population_size=50,
                violations=[_v(), _v(), _v()],
            ),
            threshold=threshold_5pct,
        )
        assert proc_fail.determination.passed is False

    def test_procedure_to_dict_excludes_threshold_and_determination(self) -> None:
        """Threshold and determination must NOT appear in the bundle-facing dict."""
        proc = Procedure(
            title="A",
            narrative="n",
            test_code="t",
            result=RunRecord(
                control_id="C",
                executed_at="2026-01-01T00:00:00Z",
                population_size=10,
                violations=[],
            ),
            threshold=Threshold(failure_threshold_pct=5.0),
        )
        d = proc.to_dict()
        assert "threshold" not in d
        assert "determination" not in d
        # code/assertion are additive (Task 4); threshold/determination remain excluded.
        assert set(d.keys()) == {"code", "title", "assertion", "narrative", "test_code", "result"}


class TestWorkpaperRollUp:
    def test_control_fails_if_any_procedure_fails(self) -> None:
        """Any-fails roll-up: one passing + one failing ⇒ control fails.

        Thresholds are set to 5% so that the OLD aggregate-count logic (3/150=2%)
        would PASS the combined view, but any-fails correctly catches branch B
        (3/50=6%) failing.
        """
        threshold_5pct = Threshold(failure_threshold_pct=5.0)
        pass_proc = Procedure(
            title="A",
            narrative="",
            test_code="...",
            result=RunRecord(
                control_id="C",
                executed_at="2026-01-01T00:00:00Z",
                population_size=100,
                violations=[],
            ),
            threshold=threshold_5pct,
        )
        fail_proc = Procedure(
            title="B",
            narrative="",
            test_code="...",
            result=RunRecord(
                control_id="C",
                executed_at="2026-01-01T00:00:00Z",
                population_size=50,
                violations=[_v(), _v(), _v()],
            ),
            threshold=threshold_5pct,
        )
        wp = Workpaper(
            control_id="C",
            title="C",
            objective="",
            narrative="",
            framework_refs={},
            procedures=[pass_proc, fail_proc],
        )
        # Aggregate view: 3 exceptions / 150 records = 2% → would pass at 5% threshold
        # (verify the "old" aggregate logic would pass)
        assert wp.records_tested == 150
        assert wp.exception_count == 3
        # Per-procedure: A passes, B fails
        assert pass_proc.determination.passed is True
        assert fail_proc.determination.passed is False
        # Roll-up: any-fails ⇒ control fails
        assert wp.determination.passed is False

    def test_control_passes_when_all_procedures_pass(self) -> None:
        """When every procedure passes, the roll-up passes."""
        proc_a = Procedure(
            title="A",
            narrative="",
            test_code="...",
            result=RunRecord(
                control_id="C",
                executed_at="2026-01-01T00:00:00Z",
                population_size=100,
                violations=[],
            ),
            threshold=Threshold(),
        )
        proc_b = Procedure(
            title="B",
            narrative="",
            test_code="...",
            result=RunRecord(
                control_id="C",
                executed_at="2026-01-01T00:00:00Z",
                population_size=50,
                violations=[],
            ),
            threshold=Threshold(),
        )
        wp = Workpaper(
            control_id="C",
            title="C",
            objective="",
            narrative="",
            framework_refs={},
            procedures=[proc_a, proc_b],
        )
        assert wp.determination.passed is True

    def test_n_equals_1_determination_unchanged(
        self, control: ControlDef, run_record: RunRecord
    ) -> None:
        """For N==1, Workpaper.determination == the single Procedure.determination."""
        wp = Workpaper.assemble(control, run_record, GENERATED_AT)
        proc = wp.procedures[0]
        assert wp.determination.passed == proc.determination.passed
        assert wp.determination.exception_count == proc.determination.exception_count
        assert wp.determination.records_tested == proc.determination.records_tested

    def test_workpaper_to_dict_excludes_procedure_threshold(
        self, control: ControlDef, run_record: RunRecord
    ) -> None:
        """to_dict() must not leak per-procedure threshold into the bundle."""
        d = Workpaper.assemble(control, run_record, GENERATED_AT).to_dict()
        for proc_dict in d["procedures"]:
            assert "threshold" not in proc_dict
            assert "determination" not in proc_dict


class TestAssembleProcedures:
    def test_assemble_procedures_builds_workpaper(self, control: ControlDef) -> None:
        """assemble_procedures classmethod builds a multi-procedure Workpaper."""
        run_a = RunRecord(
            control_id="C",
            executed_at="2026-01-01T00:00:00Z",
            population_size=100,
            violations=[],
        )
        run_b = RunRecord(
            control_id="C",
            executed_at="2026-01-01T00:00:00Z",
            population_size=50,
            violations=[_v()],
        )
        spec_a = ProcedureSpec(
            title="Proc A", narrative="narrative a", test_code="pass", threshold=Threshold()
        )
        spec_b = ProcedureSpec(
            title="Proc B", narrative="narrative b", test_code="pass", threshold=Threshold()
        )
        wp = Workpaper.assemble_procedures(
            control=control,
            procedures=[(spec_a, run_a), (spec_b, run_b)],
            generated_at=GENERATED_AT,
        )
        assert isinstance(wp, Workpaper)
        assert len(wp.procedures) == 2
        assert wp.procedures[0].title == "Proc A"
        assert wp.procedures[1].title == "Proc B"
        # Any-fails: B has 1 violation with implicit-zero → fails
        assert wp.determination.passed is False

    def test_assemble_procedures_aggregate_counts(self, control: ControlDef) -> None:
        """Headline counts aggregate across procedures."""
        run_a = RunRecord(
            control_id="C",
            executed_at="2026-01-01T00:00:00Z",
            population_size=100,
            violations=[_v()],
        )
        run_b = RunRecord(
            control_id="C",
            executed_at="2026-01-01T00:00:00Z",
            population_size=50,
            violations=[_v(), _v()],
        )
        spec_a = ProcedureSpec(
            title="A", narrative="", test_code="", threshold=Threshold(failure_threshold_pct=5.0)
        )
        spec_b = ProcedureSpec(
            title="B", narrative="", test_code="", threshold=Threshold(failure_threshold_pct=5.0)
        )
        wp = Workpaper.assemble_procedures(
            control=control,
            procedures=[(spec_a, run_a), (spec_b, run_b)],
            generated_at=GENERATED_AT,
        )
        assert wp.records_tested == 150
        assert wp.exception_count == 3
