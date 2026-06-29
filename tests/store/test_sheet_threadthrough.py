from __future__ import annotations

import io

import pandas as pd

from uticen_lite.adapters.files import source_for
from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.loader import _binding
from uticen_lite.store.migrations import migrate


def test_stored_sheet_is_read_at_runtime(tmp_path):
    (tmp_path / "data").mkdir()
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame({"id": ["A1"], "v": ["first"]}).to_excel(xw, sheet_name="First", index=False)
        pd.DataFrame({"id": ["B1"], "v": ["second"]}).to_excel(xw, sheet_name="Second", index=False)
    (tmp_path / "data" / "gl.xlsx").write_bytes(buf.getvalue())

    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_source(conn, id="gl", format="xlsx", path="data/gl.xlsx",
                       key_config={"mode": "auto"}, sheet="Second")
    repo.set_columns(conn, "gl", [
        {"original_name": "id", "display_name": "id", "data_type": "text",
         "is_key": True, "include": True, "ordinal": 0},
        {"original_name": "v", "display_name": "v", "data_type": "text",
         "is_key": False, "include": True, "ordinal": 1},
    ])
    src = repo.get_source(conn, "gl")
    conn.close()

    binding = _binding(src)
    assert binding.config.get("sheet") == "Second"
    pop = source_for(binding, tmp_path).load()
    assert pop.df["v"].tolist() == ["second"]  # NOT the first sheet


def test_binding_omits_sheet_when_none(tmp_path):
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_source(conn, id="c", format="csv", path="data/c.csv",
                       key_config={"mode": "auto"})
    repo.set_columns(conn, "c", [
        {"original_name": "id", "display_name": "id", "data_type": "text",
         "is_key": True, "include": True, "ordinal": 0},
    ])
    src = repo.get_source(conn, "c")
    conn.close()
    assert "sheet" not in _binding(src).config
