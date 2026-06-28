"""Procedure rollup in the store runner: per-check runs, merge-by-item-key, and a
distinct-items-examined population.

A pipeline control's procedures each run their checks separately (so we know which
check flagged each item), merge the violations by item-key (annotating which checks
flagged each), and report a *distinct-items-examined* population — the union of each
check's evaluated (post-filter) input frame by item-key.
"""
from pathlib import Path

import pandas as pd

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.migrations import migrate
from controlflow_sdk.store.run_service import run_control_in_store


def _seed(tmp_path: Path):
    # 4 manual JEs (+1 auto filtered out). A fails both checks; B fails 'no approval';
    # C fails 'preparer=approver'. Distinct manual JEs examined = 4 (A,B,C,E).
    df = pd.DataFrame([
        {"je_id": "A", "kind": "manual", "preparer": "approver", "approval": ""},
        {"je_id": "B", "kind": "manual", "preparer": "alice",    "approval": ""},
        {"je_id": "C", "kind": "manual", "preparer": "approver", "approval": "yes"},
        {"je_id": "E", "kind": "manual", "preparer": "bob",      "approval": "yes"},
        {"je_id": "D", "kind": "auto",   "preparer": "approver", "approval": ""},
    ])
    (tmp_path / "data").mkdir()
    df.to_csv(tmp_path / "data" / "je.csv", index=False)

    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_project(conn, name="Acme")
    repo.upsert_source(conn, id="je", format="csv", path="data/je.csv",
                       key_config={"mode": "single", "columns": ["je_id"]},
                       title="Journal Entries")
    repo.set_columns(conn, "je", [
        {"original_name": "je_id", "display_name": "je_id", "data_type": "text",
         "is_key": True, "include": True, "ordinal": 0},
        {"original_name": "kind", "display_name": "kind", "data_type": "text",
         "is_key": False, "include": True, "ordinal": 1},
        {"original_name": "preparer", "display_name": "preparer", "data_type": "text",
         "is_key": False, "include": True, "ordinal": 2},
        {"original_name": "approval", "display_name": "approval", "data_type": "text",
         "is_key": False, "include": True, "ordinal": 3},
    ])
    pipeline = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "je"},
            {"id": "flt", "type": "filter", "inputs": ["imp"],
             "config": {"logic": "all",
                        "conditions": [{"column": "kind", "op": "eq", "value": "manual"}]}},
            {"id": "t1", "type": "test", "inputs": ["flt"], "title": "preparer=approver",
             "config": {"logic": "all", "item_key_column": "je_id", "procedure_id": "p1",
                        "description_template": "Preparer equals approver on {je_id}",
                        "conditions": [{"column": "preparer", "op": "eq", "value": "approver"}]}},
            {"id": "t2", "type": "test", "inputs": ["flt"], "title": "no approval",
             "config": {"logic": "all", "item_key_column": "je_id", "procedure_id": "p1",
                        "description_template": "No approval on {je_id}",
                        "conditions": [{"column": "approval", "op": "is_empty"}]}},
        ],
        "procedures": [
            {"id": "p1", "code": "P1", "name": "Manual JE Review",
             "assertion": "Segregation of Duties", "failure_threshold_count": 0, "position": 0},
        ],
    }
    repo.upsert_control(conn, id="gl1", title="Journal Integrity", objective="o",
                        narrative="n", framework_refs={}, test_kind="pipeline",
                        pipeline=pipeline, failure_threshold_count=0)
    repo.set_control_sources(conn, "gl1", ["je"])
    return conn


def test_procedure_rollup_distinct_examined_and_merged(tmp_path: Path):
    conn = _seed(tmp_path)
    run_control_in_store(conn, tmp_path, "gl1", "2026-06-28T00:00:00Z")
    # Aggregate run is persisted; the per-procedure run carries the audit numbers.
    proc_runs = repo.list_runs_for(conn, "gl1")
    p1 = [r for r in proc_runs if r["procedure_id"] == "p1"][0]
    assert p1["population_size"] == 4          # distinct manual JEs examined (A,B,C,E)
    assert p1["failed"] == 3                   # distinct flagged items A,B,C
    # A is flagged by BOTH checks → one merged exception carrying both labels.
    violations = repo.get_run(conn, p1["run_id"])["violations"]
    a = [v for v in violations if v["item_key"] == "A"][0]
    assert sorted(a["details"]["checks"]) == ["no approval", "preparer=approver"]


def test_procedure_code_assertion_flow_to_workpaper(tmp_path: Path):
    """code/assertion must flow from ProcedureDef → ProcedureSpec → Workpaper.Procedure."""
    conn = _seed(tmp_path)
    run_control_in_store(conn, tmp_path, "gl1", "2026-06-28T00:00:00Z")

    # Read the rendered HTML workpaper — confirms the full pipeline wired correctly.
    html = (tmp_path / "target" / "workpapers" / "gl1.html").read_text(encoding="utf-8")
    assert "P1" in html, "procedure code 'P1' must appear in the rendered workpaper"
    assert "Segregation of Duties" in html, "assertion must appear in the rendered workpaper"
