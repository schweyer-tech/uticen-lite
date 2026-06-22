"""Hard export gate for Custom Python nodes (issue #25, Stage 2 §8 layer 3).

The save-time lint (layer 1) blocks a malicious node from being persisted via
the web form. The export gate is the *independent* second guard: it re-runs the
SAME allowlist deny-scan over every stored ``test_kind='pipeline'`` control at
bundle-build time and REFUSES to produce a bundle if any custom node trips —
the same posture as ``tests/test_contract_export.py`` (guard the contract where
it's *consumed*, not only where it's typed). Decision: hard BLOCK.

These tests write the offending pipeline straight to the store (bypassing the
save lint, as if it had been seeded by a migration, an older build, or a hand-
edited DB) to prove the gate stands on its own. A clean pipeline still exports.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from controlflow_sdk.model.run import RunRecord, SourceProvenance
from controlflow_sdk.model.violation import Violation
from controlflow_sdk.pipeline.lint import OFFRAMP_MESSAGE, LintError
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.export_service import build_bundle
from controlflow_sdk.store.migrations import migrate


def _engagement(tmp_path: Path):
    (tmp_path / "data").mkdir()
    (tmp_path / "target").mkdir()
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_project(conn, name="Gate Co")
    return conn


def _seed_source(conn, sid: str) -> None:
    repo.upsert_source(conn, id=sid, format="csv", path=f"data/{sid}.csv",
                       key_config={"mode": "single", "columns": ["payment_id"]})
    repo.set_columns(conn, sid, [
        {"original_name": "payment_id", "display_name": "payment_id", "is_key": True},
        {"original_name": "amount", "display_name": "amount"},
    ])


def _seed_run(conn, cid: str) -> None:
    """A minimal run so build doesn't bail on 'no runs' before reaching the gate."""
    run = RunRecord(
        control_id=cid,
        executed_at="2026-06-20T00:00:00+00:00",
        population_size=1,
        violations=[Violation.from_raw(
            {"item_key": "P1", "description": "x", "severity": "high", "details": {}})],
        provenance=[SourceProvenance(source_id="payments", path="data/payments.csv",
                                     sha256="", row_count=1)],
    )
    repo.insert_run(conn, run)


def _malicious_graph(code: str) -> dict:
    return {"nodes": [
        {"id": "imp", "type": "import", "source_id": "payments"},
        {"id": "cust", "type": "custom_python", "inputs": ["imp"],
         "config": {"flavor": "transform", "code": code}},
        {"id": "tst", "type": "test", "inputs": ["cust"],
         "config": {"logic": "any", "item_key_column": "payment_id",
                    "conditions": [{"column": "payment_id", "op": "not_empty"}]}},
    ]}


def _seed_pipeline_control(conn, cid: str, graph: dict) -> None:
    # Persist exactly what _save_from_form would, MINUS the lint — i.e. simulate a
    # node that slipped past (or predates) the save-time guard.
    repo.upsert_control(
        conn, id=cid, title="Dup", objective="o", narrative="n",
        framework_refs={"nist": []}, test_kind="pipeline",
        rule_spec=None, test_code="def test(pop, sources):\n    return []",
        pipeline=graph,
    )
    repo.set_control_sources(conn, cid, ["payments"])


def test_export_gate_blocks_open_in_custom_node(tmp_path: Path):
    conn = _engagement(tmp_path)
    _seed_source(conn, "payments")
    graph = _malicious_graph("rows = open('/etc/passwd').read()")
    _seed_pipeline_control(conn, "dup", graph)
    _seed_run(conn, "dup")
    try:
        with pytest.raises(LintError) as ei:
            build_bundle(conn, tmp_path, tmp_path / "out.zip", "2026-06-20T00:00:00+00:00")
    finally:
        conn.close()
    # The block message names the offending control + node and the offramp.
    msg = str(ei.value)
    assert "dup" in msg
    assert "cust" in msg
    assert OFFRAMP_MESSAGE in msg
    # And no bundle was written.
    assert not (tmp_path / "out.zip").exists()


def test_export_gate_blocks_read_csv_in_custom_node(tmp_path: Path):
    conn = _engagement(tmp_path)
    _seed_source(conn, "payments")
    _seed_pipeline_control(conn, "dup", _malicious_graph("rows = pd.read_csv('/x.csv')"))
    _seed_run(conn, "dup")
    try:
        with pytest.raises(LintError) as ei:
            build_bundle(conn, tmp_path, tmp_path / "out.zip", "2026-06-20T00:00:00+00:00")
    finally:
        conn.close()
    assert "read_csv" in str(ei.value)


def test_export_gate_blocks_dunder_import(tmp_path: Path):
    conn = _engagement(tmp_path)
    _seed_source(conn, "payments")
    _seed_pipeline_control(conn, "dup", _malicious_graph("m = __import__('os')\nrows = rows"))
    _seed_run(conn, "dup")
    try:
        with pytest.raises(LintError):
            build_bundle(conn, tmp_path, tmp_path / "out.zip", "2026-06-20T00:00:00+00:00")
    finally:
        conn.close()


def test_export_gate_blocks_builtins_subscript_bypass(tmp_path: Path):
    # Regression (issue #25 review): __builtins__['open'] reached a file read and
    # slipped past both the save lint and the export gate. It must now be refused.
    conn = _engagement(tmp_path)
    _seed_source(conn, "payments")
    _seed_pipeline_control(
        conn, "dup",
        _malicious_graph("leaked = __builtins__['open']('/etc/passwd').read()\nrows = rows"),
    )
    _seed_run(conn, "dup")
    try:
        with pytest.raises(LintError) as ei:
            build_bundle(conn, tmp_path, tmp_path / "out.zip", "2026-06-20T00:00:00+00:00")
    finally:
        conn.close()
    assert OFFRAMP_MESSAGE in str(ei.value)
    assert not (tmp_path / "out.zip").exists()


def test_export_gate_blocks_getattr_bypass(tmp_path: Path):
    # Regression (issue #25 review): getattr(obj, 'read_csv') reached any
    # builtin/attr by string, defeating the attr/dunder guards. Must be refused.
    conn = _engagement(tmp_path)
    _seed_source(conn, "payments")
    _seed_pipeline_control(
        conn, "dup", _malicious_graph("x = getattr(__builtins__, 'open')('/p').read()\nrows = rows")
    )
    _seed_run(conn, "dup")
    try:
        with pytest.raises(LintError) as ei:
            build_bundle(conn, tmp_path, tmp_path / "out.zip", "2026-06-20T00:00:00+00:00")
    finally:
        conn.close()
    assert OFFRAMP_MESSAGE in str(ei.value)
    assert not (tmp_path / "out.zip").exists()


def test_export_gate_allows_clean_transform_node(tmp_path: Path):
    conn = _engagement(tmp_path)
    _seed_source(conn, "payments")
    graph = _malicious_graph("rows = rows[rows['amount'].astype(float) >= 100]")
    _seed_pipeline_control(conn, "dup", graph)
    _seed_run(conn, "dup")
    try:
        out = build_bundle(conn, tmp_path, tmp_path / "out.zip", "2026-06-20T00:00:00+00:00")
    finally:
        conn.close()
    # A clean node exports normally — the gate is not a blanket block on pipelines.
    assert out.exists()


def test_export_gate_ignores_non_pipeline_controls(tmp_path: Path):
    """A rule/python control with no pipeline graph never trips the gate."""
    conn = _engagement(tmp_path)
    _seed_source(conn, "payments")
    repo.upsert_control(
        conn, id="r1", title="Rule", objective="o", narrative="n",
        framework_refs={"nist": []}, test_kind="rule",
        rule_spec={"logic": "all", "conditions": [
            {"column": "amount", "op": "not_empty"}], "severity": "low",
            "description_template": "", "item_key_column": "payment_id"},
    )
    repo.set_control_sources(conn, "r1", ["payments"])
    _seed_run(conn, "r1")
    try:
        out = build_bundle(conn, tmp_path, tmp_path / "out.zip", "2026-06-20T00:00:00+00:00")
    finally:
        conn.close()
    assert out.exists()
