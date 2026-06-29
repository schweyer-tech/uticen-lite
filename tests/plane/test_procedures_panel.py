"""Builder Procedures panel + per-Test selector round-trip (Task 5, procedures
node-grouping). A POST carrying ``graph["procedures"]`` and a Test's
``config["procedure_id"]`` must persist through the store and re-hydrate the
panel rows and the per-Test "Procedure ▾" selection on the next GET.

Also covers the round-trip-correctness decision: a LEGACY control (no stored
procedures) must pre-select each Test's EFFECTIVE owning (auto) procedure — not
"unassigned" — so a plain re-save preserves the mapping instead of dropping it.
"""
import io
import json

from uticen_lite.store import repo
from uticen_lite.store.db import connect


def _seed(client):
    csv = b"user_id,can_create\nU1,true\nU2,\n"
    client.post("/sources", data={"source_id": "users", "format": "csv"},
                files={"file": ("users.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)
    client.post("/controls", data={"id": "c1", "title": "C1", "objective": "o",
                "narrative": "n", "source_ids": ["users"], "failure_threshold_count": "0"},
                follow_redirects=False)


def _graph_with_procedure():
    return {
        "nodes": [
            {"id": "src", "type": "import", "source_id": "users"},
            {"id": "tst", "type": "test", "inputs": ["src"],
             "config": {"logic": "all", "severity": "high",
                        "item_key_column": "user_id",
                        "procedure_id": "p1",
                        "conditions": [{"column": "can_create", "op": "not_empty"}]}},
        ],
        "procedures": [
            {"id": "p1", "code": "P1", "name": "Manual JE Review",
             "assertion": "Segregation of Duties", "position": 0},
        ],
    }


def _save(client, graph):
    return client.post("/controls/c1/logic/builder",
                       data={"pipeline_json": json.dumps(graph)}, follow_redirects=False)


def test_procedures_persist_to_the_store(client, engagement):
    _seed(client)
    assert _save(client, _graph_with_procedure()).status_code == 303
    conn = connect(engagement)
    try:
        stored = repo.get_control(conn, "c1")["pipeline"]
    finally:
        conn.close()
    # _save_pipeline_graph persists the WHOLE graph dict, so `procedures` rides along.
    assert [p["name"] for p in stored["procedures"]] == ["Manual JE Review"]
    assert stored["procedures"][0]["assertion"] == "Segregation of Duties"


def test_panel_and_selector_reflect_saved_procedure(client):
    _seed(client)
    _save(client, _graph_with_procedure())
    page = client.get("/controls/c1/logic/builder").text
    # Panel row re-hydrated (id + code + name).
    assert 'data-proc-id="p1"' in page
    assert 'value="Manual JE Review"' in page
    # The per-Test selector is present and pre-selects p1.
    assert "data-procedure" in page
    assert 'value="p1" selected' in page


def test_legacy_control_preselects_effective_owner(client):
    """A control saved WITHOUT explicit procedures still pre-selects each Test's
    effective (auto) owner, so a re-save preserves the mapping (not 'unassigned')."""
    _seed(client)
    graph = _graph_with_procedure()
    graph.pop("procedures")
    graph["nodes"][1]["config"].pop("procedure_id")
    _save(client, graph)
    page = client.get("/controls/c1/logic/builder").text
    # The Builder pre-renders one auto procedure SECTION header, and the Test selector
    # pre-selects a real (non-empty) option — NOT the "— unassigned —" empty value.
    assert "data-proc-head" in page
    assert 'value="tst" selected' in page  # auto procedure id == terminal id


def test_support_card_shows_derived_chip(client):
    _seed(client)
    _save(client, _graph_with_procedure())
    page = client.get("/controls/c1/logic/builder").text
    # The Import (support) card carries a read-only derived procedure chip.
    assert "proc-chip" in page


def test_flowchart_has_procedure_color_and_legend(client):
    _seed(client)
    _save(client, _graph_with_procedure())
    fc = client.get("/controls/c1/logic/flowchart").text
    assert "fc-legend" in fc
    assert "Manual JE Review" in fc
    assert "stroke:#" in fc  # per-procedure box outline applied inline
