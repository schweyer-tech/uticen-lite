"""Store-only pipeline column + test_kind='pipeline' wiring (issue #25, Stage 1).

The pipeline graph is authoring state in the control plane's SQLite store. It is
NEVER threaded into the bundle (learnings 0001/0006): it COMPILES to the existing
rule_spec / test_code at run/build time.
"""

from __future__ import annotations

import json
from pathlib import Path

from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.migrations import SCHEMA_VERSION, migrate


def _conn(tmp_path: Path):
    conn = connect(tmp_path)
    migrate(conn)
    return conn


def test_migration_adds_pipeline_column_and_bumps_store_version(tmp_path: Path):
    conn = _conn(tmp_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(controls)").fetchall()}
    assert "pipeline" in cols
    # Store schema bumped past the previous version (NOT the bundle schema_version).
    assert SCHEMA_VERSION == 7


def test_v3_store_upgrades_to_pipeline_without_data_loss(tmp_path: Path):
    from uticen_lite.store.migrations import _STEPS

    conn = connect(tmp_path)
    conn.executescript(_STEPS[0])  # v1
    conn.executescript(_STEPS[1])  # v2
    conn.executescript(_STEPS[2])  # v3
    conn.execute("PRAGMA user_version = 3")
    conn.execute(
        "INSERT INTO controls (id, title, test_kind, rule_spec) "
        "VALUES ('C1', 'Existing', 'rule', '{}')"
    )
    conn.commit()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(controls)").fetchall()}
    assert "pipeline" not in cols

    migrate(conn)  # forward step 4 adds the pipeline column
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    cols = {r[1] for r in conn.execute("PRAGMA table_info(controls)").fetchall()}
    assert "pipeline" in cols
    row = conn.execute("SELECT id, title, pipeline FROM controls WHERE id='C1'").fetchone()
    assert row[0] == "C1" and row[1] == "Existing" and row[2] is None


def test_upsert_control_persists_pipeline_and_kind(tmp_path: Path):
    conn = _conn(tmp_path)
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "s1"},
        {"id": "t", "type": "test", "inputs": ["imp"],
         "config": {"logic": "all", "conditions": []}},
    ]}
    repo.upsert_control(
        conn, id="C1", title="Pipe", objective="", narrative="",
        framework_refs={}, test_kind="pipeline",
        rule_spec={"logic": "all", "conditions": []}, pipeline=graph,
    )
    ctrl = repo.get_control(conn, "C1")
    assert ctrl["test_kind"] == "pipeline"
    assert ctrl["pipeline"] == graph
    # The compiled artifact (rule_spec/test_code) still lands in its column.
    assert ctrl["rule_spec"] == {"logic": "all", "conditions": []}


def test_get_control_pipeline_is_none_for_non_pipeline_controls(tmp_path: Path):
    conn = _conn(tmp_path)
    repo.upsert_control(
        conn, id="C2", title="Rule", objective="", narrative="",
        framework_refs={}, test_kind="rule",
        rule_spec={"logic": "all", "conditions": []},
    )
    ctrl = repo.get_control(conn, "C2")
    assert ctrl["pipeline"] is None


def test_pipeline_column_round_trips_via_raw_json(tmp_path: Path):
    """Sanity: the column stores JSON text, decoded on read."""
    conn = _conn(tmp_path)
    graph = {"nodes": [{"id": "x", "type": "import", "source_id": "s"}]}
    repo.upsert_control(
        conn, id="C3", title="", objective="", narrative="",
        framework_refs={}, test_kind="pipeline", pipeline=graph,
    )
    raw = conn.execute("SELECT pipeline FROM controls WHERE id='C3'").fetchone()[0]
    assert json.loads(raw) == graph
