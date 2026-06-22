import argparse
from pathlib import Path

from controlflow_sdk.cli.import_cmd import import_cmd
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect


def test_import_northwind(tmp_path: Path):
    src = Path("examples/northwind-trading").resolve()
    into = tmp_path / "engagement"
    rc = import_cmd(argparse.Namespace(src=str(src), into=str(into)))
    assert rc == 0
    conn = connect(into)
    controls = repo.list_controls(conn)
    sources = repo.list_sources(conn)
    assert len(controls) == 8
    assert len(sources) == 8

    # The demo is authored as a MIX of modes (sidecar rule.yaml / pipeline.yaml
    # next to control.yaml), so the loaded engagement showcases the no-code rule
    # builder and the visual pipeline — not only the Python escape hatch.
    by_id = {c["id"]: c for c in controls}
    kinds = {cid: c["test_kind"] for cid, c in by_id.items()}
    assert set(kinds.values()) == {"rule", "pipeline", "python"}, kinds
    assert sum(k == "rule" for k in kinds.values()) >= 1
    assert sum(k == "pipeline" for k in kinds.values()) >= 1
    assert sum(k == "python" for k in kinds.values()) >= 1

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
