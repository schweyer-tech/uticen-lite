from pathlib import Path

import pytest

from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.import_service import (
    demo_source_dir,
    import_project,
    load_demo,
    reset_to_demo,
)
from uticen_lite.store.migrations import migrate
from uticen_lite.store.run_service import run_control_in_store


def _fresh_store(tmp_path: Path):
    conn = connect(tmp_path)
    migrate(conn)
    return conn


def test_import_project_returns_counts_and_rows(tmp_path: Path):
    conn = _fresh_store(tmp_path)
    n_controls, n_sources = import_project(conn, Path("examples/northwind-trading").resolve())
    assert (n_controls, n_sources) == (9, 9)
    assert len(repo.list_controls(conn)) == 9
    assert len(repo.list_sources(conn)) == 9
    assert repo.get_project(conn)["name"]
    # Author-facing source titles round-trip from sources.yaml into the store.
    invoices = repo.get_source(conn, "invoices")
    assert invoices["title"] == "Vendor Invoice Register"
    # Importing a source records a current file row carrying the file's as-of date.
    cur = repo.get_current_file(conn, "invoices")
    assert cur is not None and cur["is_current"] == 1
    assert cur["as_of_date"] == "2026-03-31"
    # ...and the file-history metadata the History tab renders: a real record
    # count (not NULL → no "—") and an upload stamp.
    assert cur["row_count"] is not None and cur["row_count"] > 0
    assert cur["uploaded_at"]


def test_demo_source_dir_has_definition_and_data():
    src = demo_source_dir()
    assert (src / "sources.yaml").is_file()
    assert (src / "cflow.yaml").is_file()
    assert list((src / "data").glob("*.csv"))


def test_load_demo_copies_data_and_is_runnable(tmp_path: Path):
    (tmp_path / "data").mkdir()
    conn = _fresh_store(tmp_path)

    n_controls, n_sources = load_demo(conn, tmp_path)
    assert (n_controls, n_sources) == (9, 9)
    # Engagement carries the friendly display name, not the "northwind-trading" slug.
    assert repo.get_project(conn)["name"] == "Northwind Trading Co."

    # CSVs landed in the engagement so stored data/<x>.csv paths resolve.
    copied = list((tmp_path / "data").glob("*.csv"))
    assert len(copied) == 9

    # End-to-end: a demo control actually runs against the copied data.
    control_id = repo.list_controls(conn)[0]["id"]
    run = run_control_in_store(conn, tmp_path, control_id, "2026-03-31T00:00:00Z")
    assert run.population_size > 0

    # Every demo-seeded source's file-history row carries a real record count and
    # an upload stamp so the History tab renders them (no "—"). Regression for the
    # demo seed creating the source_files row with NULL row_count/uploaded_at.
    for src in repo.list_sources(conn):
        cur = repo.get_current_file(conn, src["id"])
        assert cur is not None, src["id"]
        assert cur["row_count"] is not None and cur["row_count"] > 0, src["id"]
        assert cur["uploaded_at"], src["id"]


def test_reset_to_demo_wipes_junk_and_reloads(tmp_path: Path):
    (tmp_path / "data").mkdir()
    conn = _fresh_store(tmp_path)
    # Seed the kind of junk a corrupted engagement accumulates: a stray project,
    # source, and control with ids that the pristine demo never uses.
    repo.upsert_project(conn, name="Junk Engagement")
    repo.upsert_source(conn, id="junk_src", format="csv", path="data/junk.csv", key_config={})
    repo.upsert_control(
        conn, id="JUNK.1", title="junk", objective="", narrative="",
        framework_refs={}, test_kind="rule", rule_spec={},
    )

    n_controls, n_sources = reset_to_demo(conn, tmp_path)
    assert n_controls > 0 and n_sources > 0

    control_ids = {c["id"] for c in repo.list_controls(conn)}
    assert "Finance.AP.1" in control_ids  # demo restored (learning 0031: assert by id)
    assert "JUNK.1" not in control_ids  # junk control wiped
    assert repo.get_source(conn, "junk_src") is None  # junk source wiped


def test_reset_to_demo_preserves_app_settings(tmp_path: Path):
    (tmp_path / "data").mkdir()
    conn = _fresh_store(tmp_path)
    repo.upsert_project(
        conn,
        name="Junk",
        system={
            "ai": {"provider": "anthropic", "model": "claude-x"},
            "check_updates_on_launch": True,
        },
    )

    reset_to_demo(conn, tmp_path)

    system = repo.get_project(conn)["system"]
    assert system["ai"] == {"provider": "anthropic", "model": "claude-x"}
    assert system["check_updates_on_launch"] is True


def test_reset_to_demo_clears_stale_data_files(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    stale = data_dir / "stale.csv"
    stale.write_text("garbage,not,a,real,extract\n", encoding="utf-8")
    conn = _fresh_store(tmp_path)

    reset_to_demo(conn, tmp_path)

    assert not stale.exists()  # the stale extract is gone
    assert list(data_dir.glob("*.csv"))  # demo CSVs were re-copied


def test_demo_source_dir_missing_raises(monkeypatch, tmp_path: Path):
    import uticen_lite.store.import_service as mod

    # Point both candidate locations at a directory that does not exist.
    monkeypatch.setattr(mod, "__file__", str(tmp_path / "store" / "import_service.py"))
    with pytest.raises(FileNotFoundError):
        demo_source_dir()
