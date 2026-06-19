from pathlib import Path

import pandas as pd

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.migrations import migrate
from controlflow_sdk.store.run_service import run_control_in_store


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
