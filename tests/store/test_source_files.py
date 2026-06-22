from pathlib import Path

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.migrations import migrate


def _store(tmp_path: Path):
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_source(conn, id="s", format="csv", path="data/s.csv",
                       key_config={"mode": "auto"})
    return conn


def test_set_initial_then_record_archives_prior(tmp_path: Path):
    conn = _store(tmp_path)
    repo.set_initial_file(conn, source_id="s", stored_path="data/s.csv",
                          original_name="s.csv", as_of_date="2026-01-01",
                          row_count=10, uploaded_at="t0")
    cur = repo.get_current_file(conn, "s")
    assert cur["original_name"] == "s.csv" and cur["as_of_date"] == "2026-01-01"

    repo.archive_current_file(conn, "s", "data/.versions/s/t0__s.csv")
    repo.record_current_file(conn, source_id="s", stored_path="data/s.csv",
                             original_name="s2.csv", as_of_date="2026-02-01",
                             row_count=12, uploaded_at="t1")

    cur = repo.get_current_file(conn, "s")
    assert cur["as_of_date"] == "2026-02-01" and cur["row_count"] == 12
    files = repo.list_source_files(conn, "s")
    assert len(files) == 2
    assert files[0]["is_current"] == 1  # newest-first, current on top
    archived = next(f for f in files if not f["is_current"])
    assert archived["stored_path"] == "data/.versions/s/t0__s.csv"
    conn.close()


def test_set_current_file_asof_syncs_extract_date(tmp_path: Path):
    conn = _store(tmp_path)
    repo.set_initial_file(conn, source_id="s", stored_path="data/s.csv",
                          original_name="s.csv", as_of_date="2026-01-01",
                          row_count=1, uploaded_at="t0")
    repo.set_current_file_asof(conn, "s", "2026-09-09")
    assert repo.get_current_file(conn, "s")["as_of_date"] == "2026-09-09"
    assert repo.get_source(conn, "s")["extract_date"] == "2026-09-09"
    conn.close()
