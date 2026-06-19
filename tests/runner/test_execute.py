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

import pandas as pd
import pytest

from controlflow_sdk.model.control import ControlDef, FrameworkRefs, SourceBinding
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


# ---------------------------------------------------------------------------
# Multi-source / sources-dict tests (Task 1)
# ---------------------------------------------------------------------------

# Extra YAML + CSV for a second source used in multi-source tests

_SOURCES_YAML_TWO = textwrap.dedent("""\
    sources:
      - id: primary
        type: file
        config:
          path: data/primary.csv
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
      - id: secondary
        type: file
        config:
          path: data/secondary.csv
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
""")

_CONTROL_YAML_TWO = textwrap.dedent("""\
    id: multi_check
    title: Multi-Source Check
    objective: Uses two sources.
    narrative: Tests the two-arg sources dict.
    sources:
      - id: primary
      - id: secondary
    test_path: test.py
""")

_ORDERS_CSV = "order_id,amount\nORD-1,100\nORD-2,200\nORD-3,300\n"
_APPROVALS_CSV = "order_id,approved\nORD-1,yes\nORD-2,no\nORD-3,yes\n"


def _build_two_source_project(tmp_path: Path, test_py_content: str) -> Path:
    """Write a two-source project skeleton and return root."""
    (tmp_path / "cflow.yaml").write_text("name: Test Project\nsystem: {}\ndefaults: {}\n")
    (tmp_path / "sources.yaml").write_text(_SOURCES_YAML_TWO)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "primary.csv").write_text("txn_id,amount\nTX-001,5.00\nTX-002,20.00\n")
    (data_dir / "secondary.csv").write_text("txn_id\nTX-001\nTX-002\n")
    ctrl_dir = tmp_path / "controls" / "multi_check"
    ctrl_dir.mkdir(parents=True)
    (ctrl_dir / "control.yaml").write_text(_CONTROL_YAML_TWO)
    (ctrl_dir / "test.py").write_text(test_py_content)
    return tmp_path


def _load_two_source_control(
    root: Path,
) -> tuple:
    """Discover the two-source control and return (control_def, sources_dict)."""
    from controlflow_sdk.project import discover_controls
    from controlflow_sdk.project.loader import load_sources

    sources = load_sources(root)
    controls = discover_controls(root, sources=sources)
    assert len(controls) == 1
    return controls[0], sources


class TestRunControlMultiSource:
    def test_single_arg_test_unchanged(self, tmp_path: Path) -> None:
        """A 1-arg test(pop) control still runs correctly and flags the right rows."""
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
        assert record.violations[0].item_key == "TX-002"

    def test_two_arg_test_receives_all_sources_keyed_by_id(self, tmp_path: Path) -> None:
        """A 2-arg test(pop, sources) receives a dict with both source ids."""
        from controlflow_sdk.runner import run_control

        two_arg_test = textwrap.dedent("""\
            def test(pop, sources):
                assert set(sources) == {"primary", "secondary"}
                assert sources["primary"].df.equals(pop.df)
                return []
        """)
        root = _build_two_source_project(tmp_path, two_arg_test)
        control, sources = _load_two_source_control(root)
        record = run_control(
            control=control,
            sources=sources,
            root=root,
            executed_at="2026-06-16T00:00:00Z",
        )
        assert record.failed == 0

    def test_two_arg_test_can_join_across_sources(self, tmp_path: Path) -> None:
        """A 2-arg test can join primary + secondary and flag unapproved orders."""
        from controlflow_sdk.runner import run_control

        join_test = textwrap.dedent("""\
            def test(pop, sources):
                import pandas as pd
                merged = pop.df.merge(
                    sources["approvals"].df, on="order_id", how="left"
                )
                return [
                    {
                        "item_key": str(r.order_id),
                        "description": "unapproved",
                        "severity": "high",
                        "details": {},
                    }
                    for r in merged.itertuples()
                    if str(r.approved) != "yes"
                ]
        """)
        # Build a custom project with orders + approvals sources
        orders_sources_yaml = textwrap.dedent("""\
            sources:
              - id: orders
                type: file
                config:
                  path: data/orders.csv
                  format: csv
                key_config:
                  mode: single
                  columns:
                    - order_id
                column_mappings:
                  - original_name: order_id
                    display_name: Order ID
                    data_type: text
                    is_key: true
                    include: true
                  - original_name: amount
                    display_name: Amount
                    data_type: number
                    is_key: false
                    include: true
              - id: approvals
                type: file
                config:
                  path: data/approvals.csv
                  format: csv
                key_config:
                  mode: single
                  columns:
                    - order_id
                column_mappings:
                  - original_name: order_id
                    display_name: Order ID
                    data_type: text
                    is_key: true
                    include: true
                  - original_name: approved
                    display_name: Approved
                    data_type: text
                    is_key: false
                    include: true
        """)
        orders_control_yaml = textwrap.dedent("""\
            id: approval_check
            title: Approval Check
            objective: Flag unapproved orders.
            narrative: Join orders + approvals to detect unapproved.
            sources:
              - id: orders
              - id: approvals
            test_path: test.py
        """)
        (tmp_path / "cflow.yaml").write_text("name: Test Project\nsystem: {}\ndefaults: {}\n")
        (tmp_path / "sources.yaml").write_text(orders_sources_yaml)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "orders.csv").write_text(_ORDERS_CSV)
        (data_dir / "approvals.csv").write_text(_APPROVALS_CSV)
        ctrl_dir = tmp_path / "controls" / "approval_check"
        ctrl_dir.mkdir(parents=True)
        (ctrl_dir / "control.yaml").write_text(orders_control_yaml)
        (ctrl_dir / "test.py").write_text(join_test)

        from controlflow_sdk.project import discover_controls
        from controlflow_sdk.project.loader import load_sources

        sources = load_sources(tmp_path)
        controls = discover_controls(tmp_path, sources=sources)
        assert len(controls) == 1
        control = controls[0]

        record = run_control(
            control=control,
            sources=sources,
            root=tmp_path,
            executed_at="2026-06-16T00:00:00Z",
        )
        assert record.failed == 1
        assert record.violations[0].item_key == "ORD-2"

    def test_two_arg_test_that_raises_still_wrapped_as_runner_error(self, tmp_path: Path) -> None:
        """A 2-arg test that raises is wrapped as RunnerError."""
        from controlflow_sdk.runner import RunnerError, run_control

        crashing_two_arg = textwrap.dedent("""\
            def test(pop, sources):
                raise ValueError("boom")
        """)
        root = _build_two_source_project(tmp_path, crashing_two_arg)
        control, sources = _load_two_source_control(root)
        with pytest.raises(RunnerError, match="boom"):
            run_control(
                control=control,
                sources=sources,
                root=root,
                executed_at="2026-06-16T00:00:00Z",
            )


# ---------------------------------------------------------------------------
# Rule-spec execution tests (Task 11)
# ---------------------------------------------------------------------------


def _csv(tmp_path: Path) -> Path:
    p = tmp_path / "data"
    p.mkdir()
    pd.DataFrame({"user_id": ["U1", "U2"], "can_create": ["true", "true"],
                  "can_approve": ["true", "false"]}).to_csv(p / "users.csv", index=False)
    return tmp_path


def _users_binding() -> SourceBinding:
    return SourceBinding(
        id="users", type="file",
        config={"path": "data/users.csv", "format": "csv"},
        key_config={"mode": "single", "columns": ["user_id"]},
        column_mappings=[
            {"original_name": "user_id", "display_name": "User ID",
             "data_type": "text", "is_key": True, "include": True},
            {"original_name": "can_create", "display_name": "Can Create",
             "data_type": "boolean", "is_key": False, "include": True},
            {"original_name": "can_approve", "display_name": "Can Approve",
             "data_type": "boolean", "is_key": False, "include": True},
        ],
    )


def test_run_control_executes_a_rule(tmp_path: Path):
    from controlflow_sdk.runner import run_control

    root = _csv(tmp_path)
    binding = _users_binding()
    control = ControlDef(
        id="sod", title="SoD", objective="o", narrative="n",
        framework_refs=FrameworkRefs(), risk=None, sources=[binding],
        rule_spec={
            "logic": "all",
            "conditions": [
                {"column": "can_create", "op": "eq", "value": True},
                {"column": "can_approve", "op": "eq", "value": True},
            ],
            "severity": "high",
            "description_template": "User {user_id} can create and approve",
            "item_key_column": "user_id",
        },
    )
    run = run_control(control, {"users": binding}, root, "2026-03-31T00:00:00+00:00")
    assert run.population_size == 2
    assert run.failed == 1
    assert run.violations[0].item_key == "U1"
    assert run.provenance[0].source_id == "users"
