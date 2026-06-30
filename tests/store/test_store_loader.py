from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.loader import load_project_from_store
from uticen_lite.store.migrations import migrate


def _seed(tmp_path):
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_project(conn, name="Acme", framework="nist")
    repo.upsert_source(
        conn,
        id="users",
        format="csv",
        path="data/users.csv",
        key_config={"mode": "single", "columns": ["user_id"]},
    )
    repo.set_columns(
        conn,
        "users",
        [
            {
                "original_name": "user_id",
                "display_name": "User ID",
                "data_type": "text",
                "is_key": True,
                "include": True,
                "ordinal": 0,
            },
        ],
    )
    repo.upsert_control(
        conn,
        id="c1",
        title="SoD",
        objective="o",
        narrative="n",
        framework_refs={"nist": ["AC-5"]},
        test_kind="rule",
        rule_spec={"logic": "all", "conditions": [], "severity": "high"},
        failure_threshold_count=0,
    )
    repo.set_control_sources(conn, "c1", ["users"])
    return conn


def test_load_project_from_store(tmp_path):
    conn = _seed(tmp_path)
    project = load_project_from_store(conn)
    assert project.config.name == "Acme"
    assert "users" in project.sources
    binding = project.sources["users"]
    assert binding.type == "file"
    assert binding.config["format"] == "csv"
    assert binding.config["path"] == "data/users.csv"
    assert binding.column_mappings[0]["original_name"] == "user_id"
    [control] = project.controls
    assert control.id == "c1"
    assert control.test_kind == "rule"
    assert control.rule_spec["severity"] == "high"
    assert control.framework_refs.nist == ["AC-5"]
    assert control.threshold.failure_threshold_count == 0
    # bound sources resolve to the same SourceBinding objects, in order
    assert [s.id for s in control.sources] == ["users"]
