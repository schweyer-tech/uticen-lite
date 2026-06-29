import argparse
from pathlib import Path

from uticen_lite.cli.import_cmd import import_cmd
from uticen_lite.store import repo
from uticen_lite.store.db import connect


def test_import_northwind(tmp_path: Path):
    src = Path("examples/northwind-trading").resolve()
    into = tmp_path / "engagement"
    rc = import_cmd(argparse.Namespace(src=str(src), into=str(into)))
    assert rc == 0
    conn = connect(into)
    controls = repo.list_controls(conn)
    sources = repo.list_sources(conn)
    assert len(controls) == 9
    assert len(sources) == 9

    # The demo is authored entirely no-code (sidecar rule.yaml / pipeline.yaml next
    # to control.yaml): it showcases the no-code rule builder and the visual
    # pipeline, and the Python escape hatch appears only as a Custom Python NODE
    # inside a pipeline — never as a whole hand-written Python control.
    by_id = {c["id"]: c for c in controls}
    kinds = {cid: c["test_kind"] for cid, c in by_id.items()}
    assert set(kinds.values()) == {"rule", "pipeline"}, kinds
    assert sum(k == "rule" for k in kinds.values()) >= 1
    assert sum(k == "pipeline" for k in kinds.values()) >= 1

    # At least one pipeline drops to a Custom Python node for an irreducible step
    # (the escape hatch as a single node, not a standalone script).
    pipelines = [c for c in controls if c["test_kind"] == "pipeline"]
    assert any(
        any(n.get("type") == "custom_python" for n in c["pipeline"]["nodes"])
        for c in pipelines
    ), "expected at least one pipeline to use a Custom Python node"

    for c in controls:
        assert c["source_ids"]
        kind = c["test_kind"]
        if kind == "python":
            # File-based escape-hatch controls carry runnable inline source.
            assert c["test_code"] and "def test" in c["test_code"]
            assert c["rule_spec"] is None
            assert c["pipeline"] is None
        elif kind == "rule":
            # No-code rule controls carry a rule_spec; no Python.
            assert c["rule_spec"] and c["rule_spec"]["conditions"]
            assert c["test_code"] is None
            assert c["pipeline"] is None
        else:  # pipeline
            # Visual-pipeline controls keep the store-only graph AND compile to an
            # existing bundle artifact (rule_spec for the pure case, else test_code).
            assert c["pipeline"] and c["pipeline"]["nodes"]
            assert (c["rule_spec"] is not None) ^ (c["test_code"] is not None)
