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
    # every imported control has runnable test_code and a binding
    for c in controls:
        assert c["test_kind"] == "python"
        assert c["test_code"] and "def test" in c["test_code"]
        assert c["source_ids"]
