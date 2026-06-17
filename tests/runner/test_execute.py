"""Tests for controlflow_sdk.runner.execute — TDD (write first, implement second).

Fixture layout (built via tmp_path):
  <root>/
    cflow.yaml
    sources.yaml          ← defines source 'txns' pointing at txns.csv
    data/txns.csv         ← 3 rows; only id=TX-002 has amount > 15
    controls/amount_check/
      control.yaml        ← binds source 'txns'
      test.py             ← flags rows where amount > 15
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from controlflow_sdk.model.control import ControlDef, SourceBinding
from controlflow_sdk.model.run import RunRecord

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SOURCES_YAML = textwrap.dedent("""\
    sources:
      - id: txns
        type: file
        config:
          path: data/txns.csv
          format: csv
        key_config:
          mode: single
          columns:
            - txn_id
        column_mappings:
          - original_name: txn_id
            display_name: Txn ID
            data_type: text
            is_key: true
            include: true
          - original_name: amount
            display_name: Amount
            data_type: number
            is_key: false
            include: true
""")

_CSV_CONTENT = "txn_id,amount\nTX-001,5.00\nTX-002,20.00\nTX-003,8.00\n"

_CONTROL_YAML = textwrap.dedent("""\
    id: amount_check
    title: Amount Threshold Check
    objective: Flag transactions above threshold.
    narrative: Transactions with amount > 15 are flagged as violations.
    sources:
      - id: txns
    test_path: test.py
""")

_TEST_PY = textwrap.dedent("""\
    def test(pop):
        violations = []
        for _, row in pop.df.iterrows():
            if row["amount"] > 15:
                violations.append({
                    "item_key": pop.key_for(row),
                    "description": f"Amount {row['amount']} exceeds threshold",
                    "severity": "high",
                })
        return violations
""")

_TEST_PY_RETURNS_DICT = textwrap.dedent("""\
    def test(pop):
        return {"error": "oops, not a list"}
""")


def _build_project(tmp_path: Path, test_py_content: str = _TEST_PY) -> Path:
    """Write the minimal project skeleton into *tmp_path* and return it."""
    # cflow.yaml (required by Project.load but not by run_control)
    (tmp_path / "cflow.yaml").write_text("name: Test Project\nsystem: {}\ndefaults: {}\n")
    # sources.yaml
    (tmp_path / "sources.yaml").write_text(_SOURCES_YAML)
    # data directory + CSV
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "txns.csv").write_text(_CSV_CONTENT)
    # control directory + files
    ctrl_dir = tmp_path / "controls" / "amount_check"
    ctrl_dir.mkdir(parents=True)
    (ctrl_dir / "control.yaml").write_text(_CONTROL_YAML)
    (ctrl_dir / "test.py").write_text(test_py_content)
    return tmp_path


def _load_control_and_sources(
    root: Path,
) -> tuple[ControlDef, dict[str, SourceBinding]]:
    """Discover the control and return (control_def, sources_dict)."""
    from controlflow_sdk.project import discover_controls
    from controlflow_sdk.project.loader import load_sources

    sources = load_sources(root)
    controls = discover_controls(root, sources=sources)
    assert len(controls) == 1
    return controls[0], sources


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunControlFullPopulation:
    def test_run_record_population_size(self, tmp_path: Path) -> None:
        """run_control returns a RunRecord whose population_size equals the CSV row count."""
        from controlflow_sdk.runner import run_control

        root = _build_project(tmp_path)
        control, sources = _load_control_and_sources(root)
        record = run_control(
            control=control,
            sources=sources,
            root=root,
            executed_at="2026-06-16T00:00:00Z",
        )
        assert isinstance(record, RunRecord)
        assert record.population_size == 3

    def test_run_record_failed_count(self, tmp_path: Path) -> None:
        """Exactly one row violates amount > 15 (TX-002 with amount=20)."""
        from controlflow_sdk.runner import run_control

        root = _build_project(tmp_path)
        control, sources = _load_control_and_sources(root)
        record = run_control(
            control=control,
            sources=sources,
            root=root,
            executed_at="2026-06-16T00:00:00Z",
        )
        assert record.failed == 1

    def test_violation_item_key_matches_offending_row(self, tmp_path: Path) -> None:
        """The single violation's item_key must be 'TX-002' (the row with amount=20)."""
        from controlflow_sdk.runner import run_control

        root = _build_project(tmp_path)
        control, sources = _load_control_and_sources(root)
        record = run_control(
            control=control,
            sources=sources,
            root=root,
            executed_at="2026-06-16T00:00:00Z",
        )
        assert len(record.violations) == 1
        assert record.violations[0].item_key == "TX-002"

    def test_provenance_sha256_populated(self, tmp_path: Path) -> None:
        """provenance[0].sha256 must be a 64-char lowercase hex string."""
        from controlflow_sdk.runner import run_control

        root = _build_project(tmp_path)
        control, sources = _load_control_and_sources(root)
        record = run_control(
            control=control,
            sources=sources,
            root=root,
            executed_at="2026-06-16T00:00:00Z",
        )
        assert len(record.provenance) >= 1
        sha = record.provenance[0].sha256
        assert len(sha) == 64
        assert sha == sha.lower()
        assert all(c in "0123456789abcdef" for c in sha)

    def test_control_id_preserved(self, tmp_path: Path) -> None:
        """The returned RunRecord must carry the control's id."""
        from controlflow_sdk.runner import run_control

        root = _build_project(tmp_path)
        control, sources = _load_control_and_sources(root)
        record = run_control(
            control=control,
            sources=sources,
            root=root,
            executed_at="2026-06-16T00:00:00Z",
        )
        assert record.control_id == "amount_check"

    def test_executed_at_preserved(self, tmp_path: Path) -> None:
        """executed_at is passed through unchanged — runner never calls datetime.now()."""
        from controlflow_sdk.runner import run_control

        ts = "2026-06-16T12:34:56Z"
        root = _build_project(tmp_path)
        control, sources = _load_control_and_sources(root)
        record = run_control(
            control=control,
            sources=sources,
            root=root,
            executed_at=ts,
        )
        assert record.executed_at == ts


class TestRunControlErrorHandling:
    def test_non_list_return_raises_runner_error(self, tmp_path: Path) -> None:
        """A test() returning a dict (not a list) raises RunnerError mentioning the control id."""
        from controlflow_sdk.runner import RunnerError, run_control

        root = _build_project(tmp_path, test_py_content=_TEST_PY_RETURNS_DICT)
        control, sources = _load_control_and_sources(root)
        with pytest.raises(RunnerError, match="amount_check"):
            run_control(
                control=control,
                sources=sources,
                root=root,
                executed_at="2026-06-16T00:00:00Z",
            )

    def test_runner_error_is_exception(self) -> None:
        """RunnerError must be a subclass of Exception."""
        from controlflow_sdk.runner import RunnerError

        assert issubclass(RunnerError, Exception)

    def test_test_function_exception_wrapped_in_runner_error(self, tmp_path: Path) -> None:
        """If test() raises an exception, it's wrapped in RunnerError with the control id."""
        from controlflow_sdk.runner import RunnerError, run_control

        crashing_test = textwrap.dedent("""\
            def test(pop):
                raise ValueError("intentional crash")
        """)
        root = _build_project(tmp_path, test_py_content=crashing_test)
        control, sources = _load_control_and_sources(root)
        with pytest.raises(RunnerError, match="amount_check"):
            run_control(
                control=control,
                sources=sources,
                root=root,
                executed_at="2026-06-16T00:00:00Z",
            )

    def test_runner_error_strips_sdk_internal_frames(self, tmp_path: Path) -> None:
        """RunnerError must NOT contain SDK-internal or site-packages frames.

        The message must still name the control id and the exception text,
        but SDK frames (controlflow_sdk/runner/execute.py, site-packages) must
        be absent so the user sees only their own test.py context.
        """
        from controlflow_sdk.runner import RunnerError, run_control

        crashing_test = textwrap.dedent("""\
            def test(pop):
                raise ValueError("intentional crash for frame test")
        """)
        root = _build_project(tmp_path, test_py_content=crashing_test)
        control, sources = _load_control_and_sources(root)
        with pytest.raises(RunnerError) as exc_info:
            run_control(
                control=control,
                sources=sources,
                root=root,
                executed_at="2026-06-16T00:00:00Z",
            )
        msg = str(exc_info.value)
        # Must still name the control id and exception text
        assert "amount_check" in msg
        assert "intentional crash for frame test" in msg
        # Must NOT contain SDK-internal paths
        assert "controlflow_sdk/runner/execute.py" not in msg
        assert "site-packages" not in msg

    def test_malformed_violation_raises_runner_error(self, tmp_path: Path) -> None:
        """A violation missing 'description' is rejected and raises RunnerError."""
        from controlflow_sdk.runner import RunnerError, run_control

        bad_violation_test = textwrap.dedent("""\
            def test(pop):
                return [{"item_key": "TX-001"}]  # missing 'description'
        """)
        root = _build_project(tmp_path, test_py_content=bad_violation_test)
        control, sources = _load_control_and_sources(root)
        with pytest.raises(RunnerError, match="amount_check"):
            run_control(
                control=control,
                sources=sources,
                root=root,
                executed_at="2026-06-16T00:00:00Z",
            )
