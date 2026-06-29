"""Route tests for the Logic ▸ Trace single-record trace (issue #29)."""
from __future__ import annotations

import io
import json

from uticen_lite.store import repo


def _make_source(client, sid, csv_bytes: bytes) -> None:
    client.post(
        "/sources",
        data={"source_id": sid, "format": "csv"},
        files={"file": (f"{sid}.csv", io.BytesIO(csv_bytes), "text/csv")},
        follow_redirects=False,
    )


def _conn(client):
    from uticen_lite.store.db import connect
    return connect(client.app.state.project_root)


def _configure_source(
    client, sid="invoices", key="invoice_id", number_cols=("amount",)
) -> None:
    """Configure a source via the real save route, setting key_config and dtypes."""
    data: dict[str, str] = {"key_columns": key}
    conn = _conn(client)
    try:
        cols = repo.get_source(conn, sid)["columns"]
    finally:
        conn.close()
    for c in cols:
        name = c["original_name"]
        data[f"include__{name}"] = "on"
        data[f"data_type__{name}"] = "number" if name in number_cols else "text"
    client.post(f"/sources/{sid}", data=data, follow_redirects=False)


def _make_control(client, cid="C1") -> None:
    client.post("/controls", data={
        "id": cid, "title": "Trace Test", "objective": "o", "narrative": "n",
    }, follow_redirects=False)


def _save_pipeline(client, cid, graph):
    return client.post(f"/controls/{cid}/logic/builder",
                       data={"pipeline_json": json.dumps(graph)},
                       follow_redirects=False)


_INVOICES = (
    b"invoice_id,amount\n"
    b"INV001,100\nINV002,200\nINV003,300\nINV004,400\nINV005,500\n"
)


def _seeded(client, conditions=None):
    _make_source(client, "invoices", _INVOICES)
    _configure_source(client)
    cid = "TR1"
    _make_control(client, cid)
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "invoices", "inputs": []},
        {"id": "tst", "type": "test", "inputs": ["imp"], "config": {
            "logic": "all",
            "conditions": conditions if conditions is not None
            else [{"column": "amount", "op": "gt", "value": 100}],
        }},
    ]}
    _save_pipeline(client, cid, graph)
    return client, cid


def test_trace_tab_is_linked_on_builder(client):
    c, cid = _seeded(client)
    r = c.get(f"/controls/{cid}/logic/builder")
    assert r.status_code == 200
    assert f"/controls/{cid}/logic/trace" in r.text


def test_trace_picker_shows_example_keys(client):
    c, cid = _seeded(client)
    r = c.get(f"/controls/{cid}/logic/trace")
    assert r.status_code == 200
    assert "INV001" in r.text  # an example-key chip


def test_flagged_record_renders_flagged_and_condition(client):
    c, cid = _seeded(client)
    r = c.get(f"/controls/{cid}/logic/trace", params={"key": "INV005"})
    assert r.status_code == 200
    assert "Flagged as an exception" in r.text
    assert "amount" in r.text and "gt" in r.text


def test_passing_record_renders_passed(client):
    c, cid = _seeded(client)
    r = c.get(f"/controls/{cid}/logic/trace", params={"key": "INV001"})
    assert r.status_code == 200
    assert "Passed" in r.text


def test_missing_key_renders_not_found(client):
    c, cid = _seeded(client)
    r = c.get(f"/controls/{cid}/logic/trace", params={"key": "ZZZ"})
    assert r.status_code == 200
    assert "No record" in r.text


def test_python_control_degrades(client):
    _make_source(client, "invoices", _INVOICES)
    _configure_source(client)
    cid = "PYC"
    _make_control(client, cid)
    c = client
    # Bind the source, then author raw Python via the python tab.
    _save_pipeline(c, cid, {"nodes": [
        {"id": "imp", "type": "import", "source_id": "invoices", "inputs": []},
        {"id": "tst", "type": "test", "inputs": ["imp"],
         "config": {"logic": "all", "conditions": []}},
    ]})
    c.post(f"/controls/{cid}/logic/convert", follow_redirects=False)
    r = c.get(f"/controls/{cid}/logic/trace", params={"key": "INV001"})
    assert r.status_code == 200
    assert "rule builder" in r.text


def test_bad_condition_column_never_500s(client):
    c, cid = _seeded(client, conditions=[{"column": "nope", "op": "gt", "value": 1}])
    r = c.get(f"/controls/{cid}/logic/trace", params={"key": "INV001"})
    assert r.status_code == 200  # never 500 (learnings 0013/0033)


def test_dropped_record_shows_did_not_reach_not_passed(client):
    """A record dropped by an upstream Filter must show 'Did not reach this Test', not 'Passed'."""
    _make_source(client, "invoices", _INVOICES)
    _configure_source(client)
    cid = "DRP"
    _make_control(client, cid)
    _save_pipeline(client, cid, {"nodes": [
        {"id": "imp", "type": "import", "source_id": "invoices", "inputs": []},
        {"id": "flt", "type": "filter", "inputs": ["imp"], "config": {
            "logic": "all", "conditions": [{"column": "amount", "op": "gt", "value": 200}]}},
        {"id": "tst", "type": "test", "inputs": ["flt"], "config": {
            "logic": "all", "conditions": [{"column": "amount", "op": "gt", "value": 100}]}},
    ]})
    # INV001 has amount=100, which is not > 200, so it is dropped at the Filter
    # and never arrives at the Test.
    r = client.get(f"/controls/{cid}/logic/trace", params={"key": "INV001"})
    assert r.status_code == 200
    assert "Did not reach this Test" in r.text
    assert "Passed — not flagged" not in r.text
