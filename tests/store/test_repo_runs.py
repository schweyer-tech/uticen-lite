from controlflow_sdk.model.run import RunRecord, SourceProvenance
from controlflow_sdk.model.violation import Severity, Violation
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.migrations import migrate


def _db(tmp_path):
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_control(conn, id="c1", title="t", objective="o", narrative="n",
                        framework_refs={}, test_kind="python", test_code="x")
    return conn


def _run():
    return RunRecord(
        control_id="c1", executed_at="2026-03-31T00:00:00+00:00", population_size=3,
        violations=[Violation(item_key="U1", description="bad", severity=Severity.HIGH,
                              details={"amount": 5})],
        provenance=[SourceProvenance(source_id="users", path="data/users.csv",
                                     sha256="abc", row_count=3)],
    )


def test_insert_and_get_run(tmp_path):
    conn = _db(tmp_path)
    run = _run()
    repo.insert_run(conn, run)
    got = repo.get_run(conn, run.run_id)
    assert got["control_id"] == "c1"
    assert got["failed"] == 1 and got["total"] == 3
    assert got["violations"][0]["item_key"] == "U1"
    assert got["violations"][0]["details"] == {"amount": 5}
    assert got["provenance"][0]["sha256"] == "abc"


def test_latest_run(tmp_path):
    conn = _db(tmp_path)
    older = _run()
    newer = RunRecord(control_id="c1", executed_at="2026-04-01T00:00:00+00:00",
                      population_size=3, violations=[], provenance=[])
    repo.insert_run(conn, older)
    repo.insert_run(conn, newer)
    assert repo.latest_run(conn, "c1")["run_id"] == newer.run_id
    assert len(repo.list_runs_for(conn, "c1")) == 2
