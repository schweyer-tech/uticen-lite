"""Saving a pipeline whose endpoint is not a Test must render the offending node
card red (inline error), not only a top banner (2026-06-27 review)."""

import io
import json


def _seed(client):
    client.post(
        "/sources",
        data={"source_id": "users", "format": "csv"},
        files={"file": ("users.csv", io.BytesIO(b"user_id\nU1\n"), "text/csv")},
        follow_redirects=False,
    )
    client.post(
        "/controls",
        data={
            "id": "c1",
            "title": "C1",
            "objective": "o",
            "narrative": "n",
            "source_ids": ["users"],
            "failure_threshold_count": "0",
        },
        follow_redirects=False,
    )


def test_non_terminal_endpoint_pins_error_to_node(client):
    _seed(client)
    # A lone Import node is a non-terminal sink.
    graph = {"nodes": [{"id": "imp_access_accounts", "type": "import", "source_id": "users"}]}
    r = client.post(
        "/controls/c1/logic/builder",
        data={"pipeline_json": json.dumps(graph)},
        follow_redirects=False,
    )
    # a validation failure re-renders the builder (422), not a 500
    assert r.status_code in (200, 422), r.text
    # the offending card is rendered red and carries the inline message
    assert "node-error" in r.text
    assert "node-err-msg" in r.text
    assert "must end in a Test" in r.text
    # the bare "node 'id':" prefix is stripped from the inline message (it's on the card)
    assert "node 'imp_access_accounts':" not in r.text.split("node-err-msg", 1)[1][:200]
