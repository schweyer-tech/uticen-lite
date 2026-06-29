from __future__ import annotations

import hashlib

from uticen_lite.model.run import RunRecord, SourceProvenance
from uticen_lite.model.violation import Severity, Violation

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_violation(key: str = "INV-1", severity: str = "medium") -> Violation:
    return Violation(item_key=key, description="test violation", severity=Severity.coerce(severity))


def _make_run(
    population_size: int = 10,
    violations: list[Violation] | None = None,
    executed_at: str = "2026-06-16T12:00:00Z",
    control_id: str = "ctrl-abc",
) -> RunRecord:
    provenance = [
        SourceProvenance(
            source_id="src-1",
            path="s3://bucket/data.csv",
            sha256="aabbcc",
            row_count=population_size,
        )
    ]
    return RunRecord(
        control_id=control_id,
        executed_at=executed_at,
        population_size=population_size,
        violations=violations or [],
        provenance=provenance,
    )


# ── SourceProvenance tests ────────────────────────────────────────────────────


def test_source_provenance_fields():
    sp = SourceProvenance(source_id="s1", path="/data/file.csv", sha256="deadbeef", row_count=42)
    assert sp.source_id == "s1"
    assert sp.path == "/data/file.csv"
    assert sp.sha256 == "deadbeef"
    assert sp.row_count == 42


# ── RunRecord computed property tests ─────────────────────────────────────────


def test_failed_equals_violation_count():
    run = _make_run(population_size=10, violations=[_make_violation("A"), _make_violation("B")])
    assert run.failed == 2


def test_passed_equals_population_minus_failed():
    run = _make_run(population_size=10, violations=[_make_violation("A"), _make_violation("B")])
    assert run.passed == 8


def test_pass_rate_standard_case():
    run = _make_run(population_size=10, violations=[_make_violation("A"), _make_violation("B")])
    assert run.pass_rate == 80.0


def test_pass_rate_zero_population_no_division_error():
    run = _make_run(population_size=0, violations=[])
    assert run.pass_rate == 0.0


def test_passed_cannot_go_negative():
    # More violations than population — passed is clamped to 0
    violations = [_make_violation(f"V{i}") for i in range(5)]
    run = _make_run(population_size=2, violations=violations)
    assert run.passed == 0


def test_summary_format():
    run = _make_run(population_size=10, violations=[_make_violation("A"), _make_violation("B")])
    assert run.summary == "2 violation(s) across 10 record(s)"


def test_summary_zero_violations():
    run = _make_run(population_size=10, violations=[])
    assert run.summary == "0 violation(s) across 10 record(s)"


# ── run_id determinism tests ──────────────────────────────────────────────────


def test_run_id_deterministic_same_inputs():
    run_a = _make_run(executed_at="2026-06-16T12:00:00Z")
    run_b = _make_run(executed_at="2026-06-16T12:00:00Z")
    assert run_a.run_id == run_b.run_id


def test_run_id_differs_with_different_executed_at():
    run_a = _make_run(executed_at="2026-06-16T12:00:00Z")
    run_b = _make_run(executed_at="2026-06-16T13:00:00Z")
    assert run_a.run_id != run_b.run_id


def test_run_id_differs_with_different_control_id():
    run_a = _make_run(control_id="ctrl-1", executed_at="2026-06-16T12:00:00Z")
    run_b = _make_run(control_id="ctrl-2", executed_at="2026-06-16T12:00:00Z")
    assert run_a.run_id != run_b.run_id


def test_run_id_is_16_chars():
    run = _make_run()
    assert len(run.run_id) == 16


def test_run_id_manual_derivation():
    """Verify the exact derivation: sha256(control_id + executed_at + prov_hashes)[:16]."""
    provenance = [SourceProvenance(source_id="s1", path="/f.csv", sha256="aabb", row_count=5)]
    run = RunRecord(
        control_id="ctrl-x",
        executed_at="2026-01-01T00:00:00Z",
        population_size=5,
        violations=[],
        provenance=provenance,
    )
    raw = "ctrl-x" + "2026-01-01T00:00:00Z" + "aabb"
    expected = hashlib.sha256(raw.encode()).hexdigest()[:16]
    assert run.run_id == expected


# ── to_dict tests ─────────────────────────────────────────────────────────────


def test_to_dict_includes_test_results_columns():
    run = _make_run(population_size=10, violations=[_make_violation("A"), _make_violation("B")])
    d = run.to_dict()
    assert d["passed"] == 8
    assert d["failed"] == 2
    assert d["total"] == 10
    assert d["pass_rate"] == 80.0
    assert d["summary"] == "2 violation(s) across 10 record(s)"


def test_to_dict_total_equals_population_size():
    run = _make_run(population_size=50)
    d = run.to_dict()
    assert d["total"] == 50


def test_to_dict_details_has_violations_list():
    v = _make_violation("INV-99", "high")
    run = _make_run(population_size=5, violations=[v])
    d = run.to_dict()
    assert "details" in d
    assert "violations" in d["details"]
    vlist = d["details"]["violations"]
    assert isinstance(vlist, list)
    assert len(vlist) == 1
    assert vlist[0]["item_key"] == "INV-99"


def test_to_dict_includes_control_id_run_id_executed_at():
    run = _make_run(control_id="ctrl-abc", executed_at="2026-06-16T12:00:00Z")
    d = run.to_dict()
    assert d["control_id"] == "ctrl-abc"
    assert d["run_id"] == run.run_id
    assert d["executed_at"] == "2026-06-16T12:00:00Z"


def test_to_dict_includes_provenance():
    run = _make_run()
    d = run.to_dict()
    assert "provenance" in d
    assert isinstance(d["provenance"], list)
    assert d["provenance"][0]["source_id"] == "src-1"


def test_to_dict_pass_rate_zero_population():
    run = _make_run(population_size=0)
    d = run.to_dict()
    assert d["pass_rate"] == 0.0
    assert d["passed"] == 0
    assert d["failed"] == 0
    assert d["total"] == 0
