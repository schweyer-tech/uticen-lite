from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.migrations import migrate


def _db(tmp_path):
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_source(conn, id="users", format="csv", path="data/users.csv", key_config={})
    return conn


def test_rule_control_roundtrip(tmp_path):
    conn = _db(tmp_path)
    spec = {"logic": "all", "conditions": [{"column": "x", "op": "eq", "value": 1}],
            "severity": "high"}
    repo.upsert_control(
        conn, id="c1", title="SoD", objective="o", narrative="n",
        framework_refs={"nist": ["AC-5"]}, test_kind="rule", rule_spec=spec,
        failure_threshold_count=0,
    )
    repo.set_control_sources(conn, "c1", ["users"])
    c = repo.get_control(conn, "c1")
    assert c["test_kind"] == "rule"
    assert c["rule_spec"] == spec
    assert c["framework_refs"] == {"nist": ["AC-5"]}
    assert c["source_ids"] == ["users"]
    assert c["failure_threshold_count"] == 0


def test_python_control_roundtrip(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_control(
        conn, id="c2", title="t", objective="o", narrative="n",
        framework_refs={}, test_kind="python",
        test_code="def test(pop):\n    return []",
    )
    c = repo.get_control(conn, "c2")
    assert c["test_kind"] == "python"
    assert c["test_code"].startswith("def test(pop)")
    assert c["rule_spec"] is None


def test_set_control_sources_orders_by_index(tmp_path):
    conn = _db(tmp_path)
    repo.upsert_source(conn, id="b", format="csv", path="data/b.csv", key_config={})
    repo.upsert_control(conn, id="c3", title="t", objective="o", narrative="n",
                        framework_refs={}, test_kind="python", test_code="x")
    repo.set_control_sources(conn, "c3", ["b", "users"])
    assert repo.get_control(conn, "c3")["source_ids"] == ["b", "users"]
