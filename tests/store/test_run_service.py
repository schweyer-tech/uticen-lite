import json
from pathlib import Path

import pandas as pd

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.migrations import migrate
from controlflow_sdk.store.run_service import run_control_in_store

# ---------------------------------------------------------------------------
# Forked (2-terminal) pipeline control
# ---------------------------------------------------------------------------

def _seed_forked_pipeline(tmp_path: Path):
    """A forked pipeline control with two terminal Test nodes sharing one Import.

    Data:
      item_id | value
      I1      | 5      ← flagged by branch B (value < 10)
      I2      | 8      ← flagged by branch B (value < 10)
      I3      | 150    ← flagged by branch A (value > 100)
      I4      | 50     ← not flagged by either branch

    Branch A: items where value > 100  →  1 exception (I3)
    Branch B: items where value < 10   →  2 exceptions (I1, I2)
    """
    (tmp_path / "data").mkdir()
    pd.DataFrame({
        "item_id": ["I1", "I2", "I3", "I4"],
        "value": [5, 8, 150, 50],
    }).to_csv(tmp_path / "data" / "inv.csv", index=False)

    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_project(conn, name="Acme")
    repo.upsert_source(conn, id="inv", format="csv", path="data/inv.csv",
                       key_config={"mode": "single", "columns": ["item_id"]})
    repo.set_columns(conn, "inv", [
        {"original_name": "item_id", "display_name": "Item ID", "data_type": "text",
         "is_key": True, "include": True, "ordinal": 0},
        {"original_name": "value", "display_name": "Value", "data_type": "number",
         "is_key": False, "include": True, "ordinal": 1},
    ])

    # Forked pipeline: Import("inv") → Test("a") and Import("inv") → Test("b")
    pipeline = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "inv"},
            {
                "id": "a", "type": "test", "inputs": ["imp"],
                "config": {
                    "logic": "all",
                    "conditions": [{"column": "value", "op": "gt", "value": 100}],
                    "severity": "high",
                    "description_template": "Item {item_id} value too high",
                    "item_key_column": "item_id",
                    "title": "High-value items",
                },
            },
            {
                "id": "b", "type": "test", "inputs": ["imp"],
                "config": {
                    "logic": "all",
                    "conditions": [{"column": "value", "op": "lt", "value": 10}],
                    "severity": "medium",
                    "description_template": "Item {item_id} value too low",
                    "item_key_column": "item_id",
                    "title": "Low-value items",
                },
            },
        ]
    }
    repo.upsert_control(conn, id="forked", title="Forked control", objective="o",
                        narrative="n", framework_refs={"nist": ["AC-5"]},
                        test_kind="pipeline", pipeline=pipeline,
                        failure_threshold_count=0)
    repo.set_control_sources(conn, "forked", ["inv"])
    return conn


def test_forked_control_runs_one_result_per_procedure(tmp_path: Path):
    """For a 2-terminal pipeline: three RunRecords are persisted.

    Two procedure-specific runs (procedure_id in {"a","b"}) plus one union
    aggregate run (procedure_id=="") that is persisted so the run-view route
    can look it up via the run_id returned by run_control_in_store.
    """
    conn = _seed_forked_pipeline(tmp_path)
    result = run_control_in_store(conn, tmp_path, "forked", "2026-03-31T00:00:00+00:00")

    # Three runs persisted: one per terminal procedure + one union aggregate.
    all_runs = repo.list_runs_for(conn, "forked")
    assert len(all_runs) == 3, f"expected 3 runs, got {len(all_runs)}"

    proc_ids = {r["procedure_id"] for r in all_runs}
    assert proc_ids == {"a", "b", ""}, f"unexpected procedure_ids: {proc_ids}"

    # Branch A (value > 100): 4 records total, 1 exception (I3).
    run_a = next(r for r in all_runs if r["procedure_id"] == "a")
    assert run_a["failed"] == 1, f"branch A: expected 1 exception, got {run_a['failed']}"
    assert run_a["total"] == 4

    # Branch B (value < 10): 4 records total, 2 exceptions (I1, I2).
    run_b = next(r for r in all_runs if r["procedure_id"] == "b")
    assert run_b["failed"] == 2, f"branch B: expected 2 exceptions, got {run_b['failed']}"
    assert run_b["total"] == 4

    # Union aggregate: combines all violations (3 total: 1 from A, 2 from B).
    run_union = next(r for r in all_runs if r["procedure_id"] == "")
    assert run_union["failed"] == 3, f"union: expected 3 exceptions, got {run_union['failed']}"
    assert run_union["total"] == 4

    # The workpaper files are written once under the control id.
    assert (tmp_path / "target" / "workpapers" / "forked.html").exists()
    assert (tmp_path / "target" / "workpapers" / "forked.md").exists()
    assert (tmp_path / "target" / "evidence" / "forked-violations.json").exists()

    # Evidence file contains the union of all violations (3 total: 1 from A, 2 from B).
    violations = json.loads(
        (tmp_path / "target" / "evidence" / "forked-violations.json").read_text()
    )
    assert len(violations) == 3, f"expected 3 union violations, got {len(violations)}"

    # The returned record is the union aggregate (back-compat: callers get one record).
    assert result.control_id == "forked"
    # The union aggregate run_id is stored in the DB so the run-view route can look it up.
    assert repo.get_run(conn, result.run_id) is not None, (
        "union aggregate run_id not found in DB — run-view would 404"
    )


def _seed(tmp_path: Path):
    (tmp_path / "data").mkdir()
    pd.DataFrame({"user_id": ["U1", "U2"], "can_create": ["true", "true"],
                  "can_approve": ["true", "false"]}).to_csv(
        tmp_path / "data" / "users.csv", index=False)
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_project(conn, name="Acme")
    repo.upsert_source(conn, id="users", format="csv", path="data/users.csv",
                       key_config={"mode": "single", "columns": ["user_id"]})
    repo.set_columns(conn, "users", [
        {"original_name": "user_id", "display_name": "User ID", "data_type": "text",
         "is_key": True, "include": True, "ordinal": 0},
        {"original_name": "can_create", "display_name": "Can Create",
         "data_type": "boolean", "is_key": False, "include": True, "ordinal": 1},
        {"original_name": "can_approve", "display_name": "Can Approve",
         "data_type": "boolean", "is_key": False, "include": True, "ordinal": 2},
    ])
    repo.upsert_control(conn, id="sod", title="SoD", objective="o", narrative="n",
                        framework_refs={"nist": ["AC-5"]}, test_kind="rule",
                        rule_spec={"logic": "all", "conditions": [
                            {"column": "can_create", "op": "eq", "value": True},
                            {"column": "can_approve", "op": "eq", "value": True}],
                            "severity": "high",
                            "description_template": "User {user_id}",
                            "item_key_column": "user_id"},
                        failure_threshold_count=0)
    repo.set_control_sources(conn, "sod", ["users"])
    return conn


def test_run_persists_and_renders(tmp_path: Path):
    conn = _seed(tmp_path)
    run = run_control_in_store(conn, tmp_path, "sod", "2026-03-31T00:00:00+00:00")
    assert run.failed == 1
    # persisted
    assert repo.latest_run(conn, "sod")["run_id"] == run.run_id
    # rendered
    assert (tmp_path / "target" / "workpapers" / "sod.html").exists()
    assert (tmp_path / "target" / "workpapers" / "sod.md").exists()
    assert (tmp_path / "target" / "evidence" / "sod-violations.json").exists()
    html = (tmp_path / "target" / "workpapers" / "sod.html").read_text()
    assert "<!doctype html>" in html.lower()


def _seed_inline(tmp_path: Path):
    """An inline-python control whose test_code is embedded directly in the store."""
    (tmp_path / "data").mkdir()
    pd.DataFrame({"user_id": ["U1", "U2"], "can_create": ["true", "true"]}).to_csv(
        tmp_path / "data" / "users.csv", index=False)
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_project(conn, name="Acme")
    repo.upsert_source(conn, id="users", format="csv", path="data/users.csv",
                       key_config={"mode": "single", "columns": ["user_id"]})
    repo.set_columns(conn, "users", [
        {"original_name": "user_id", "display_name": "User ID", "data_type": "text",
         "is_key": True, "include": True, "ordinal": 0},
        {"original_name": "can_create", "display_name": "Can Create",
         "data_type": "boolean", "is_key": False, "include": True, "ordinal": 1},
    ])
    repo.upsert_control(conn, id="inline", title="Inline", objective="o", narrative="n",
                        framework_refs={"nist": ["AC-2"]}, test_kind="python",
                        test_code="# inline\ndef test(pop):\n    return []\n",
                        failure_threshold_count=0)
    repo.set_control_sources(conn, "inline", ["users"])
    return conn


def test_run_persists_inline_control_test_code(tmp_path: Path):
    """An inline-python control's test_code is rendered verbatim in the workpaper.

    Pins the inline branch of run_service's resolver wrapper (the rule branch is
    exercised by test_run_persists_and_renders).
    """
    conn = _seed_inline(tmp_path)
    run_control_in_store(conn, tmp_path, "inline", "2026-03-31T00:00:00+00:00")
    md = (tmp_path / "target" / "workpapers" / "inline.md").read_text()
    assert "# inline" in md


def _seed_cross_source(tmp_path: Path):
    """A terminated-access control: AD accounts whose user is NOT in the HR roster."""
    (tmp_path / "data").mkdir()
    # access: U1, U2, U3 hold accounts; only U1 + U3 are still employed.
    pd.DataFrame({"user_id": ["U1", "U2", "U3"]}).to_csv(
        tmp_path / "data" / "access.csv", index=False)
    pd.DataFrame({"employee_id": ["U1", "U3"], "name": ["Ann", "Cara"]}).to_csv(
        tmp_path / "data" / "hr_roster.csv", index=False)
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_project(conn, name="Acme")
    repo.upsert_source(conn, id="access", format="csv", path="data/access.csv",
                       key_config={"mode": "single", "columns": ["user_id"]})
    repo.set_columns(conn, "access", [
        {"original_name": "user_id", "display_name": "User ID", "data_type": "text",
         "is_key": True, "include": True, "ordinal": 0}])
    repo.upsert_source(conn, id="hr_roster", format="csv", path="data/hr_roster.csv",
                       key_config={"mode": "single", "columns": ["employee_id"]})
    repo.set_columns(conn, "hr_roster", [
        {"original_name": "employee_id", "display_name": "Employee ID",
         "data_type": "text", "is_key": True, "include": True, "ordinal": 0},
        {"original_name": "name", "display_name": "Name", "data_type": "text",
         "is_key": False, "include": True, "ordinal": 1}])
    repo.upsert_control(conn, id="term", title="Terminated access", objective="o",
                        narrative="n", framework_refs={"nist": ["AC-2"]},
                        test_kind="rule",
                        rule_spec={"logic": "all", "conditions": [
                            {"op": "not_exists_in", "column": "user_id",
                             "other_source": "hr_roster", "this_key": "user_id",
                             "other_key": "employee_id"}],
                            "severity": "high",
                            "description_template": "User {user_id} retains access",
                            "item_key_column": "user_id"},
                        failure_threshold_count=0)
    # access first (primary), hr_roster second (lookup B)
    repo.set_control_sources(conn, "term", ["access", "hr_roster"])
    return conn


def test_run_cross_source_terminated_access(tmp_path: Path):
    conn = _seed_cross_source(tmp_path)
    run = run_control_in_store(conn, tmp_path, "term", "2026-03-31T00:00:00+00:00")
    # Only U2 (terminated but still has an account) is flagged.
    assert run.failed == 1
    assert [v.item_key for v in run.violations] == ["U2"]
    # The workpaper procedure test_code is the generated multi-source Python.
    html = (tmp_path / "target" / "workpapers" / "term.html").read_text()
    assert "def test(pop, sources)" in html
    assert (tmp_path / "target" / "workpapers" / "term.md").exists()
