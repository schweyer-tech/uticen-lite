"""Narrative is a wrapping/expandable textarea with a pop-out modal; the Custom
Python node code gets the same pop-out with CodeMirror syntax highlighting.
2026-06-27 review."""

import io
import json
import re


def _seed(client):
    csv = b"user_id,can_create\nU1,true\n"
    client.post(
        "/sources",
        data={"source_id": "users", "format": "csv"},
        files={"file": ("users.csv", io.BytesIO(csv), "text/csv")},
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


def _save(client, with_python=False):
    nodes = [{"id": "imp", "type": "import", "source_id": "users"}]
    if with_python:
        nodes.append(
            {
                "id": "py",
                "type": "custom_python",
                "inputs": ["imp"],
                "config": {"flavor": "test", "code": "out = []\nreturn out"},
            }
        )
    else:
        nodes.append(
            {
                "id": "tst",
                "type": "test",
                "inputs": ["imp"],
                "config": {
                    "logic": "all",
                    "severity": "high",
                    "item_key_column": "user_id",
                    "description_template": "User {user_id}",
                    "conditions": [{"column": "can_create", "op": "eq", "value": True}],
                },
            }
        )
    return client.post(
        "/controls/c1/logic/builder",
        data={"pipeline_json": json.dumps({"nodes": nodes})},
        follow_redirects=False,
    )


def test_narrative_is_a_textarea_with_expand(client):
    _seed(client)
    _save(client)
    page = client.get("/controls/c1/logic/builder")
    # narrative is now a textarea (wraps/expands), not a single-line input
    # (order-independent: the textarea also carries a `for`/`id` a11y hook)
    assert re.search(r"<textarea[^>]*\bdata-narrative\b", page.text)
    assert "openFieldModal(this, 'narrative')" in page.text
    # the pop-out modal + its editor exist on the page
    assert 'id="field-modal"' in page.text


def test_python_node_has_popout_and_codemirror(client):
    _seed(client)
    _save(client, with_python=True)
    page = client.get("/controls/c1/logic/builder")
    assert "openFieldModal(this, 'code')" in page.text
    # CodeMirror is loaded on the builder so the pop-out can syntax-highlight Python
    assert "/static/codemirror.min.js" in page.text
    assert "/static/codemirror-python.min.js" in page.text


def test_narrative_roundtrips_multiline(client):
    _seed(client)
    nodes = [
        {
            "id": "imp",
            "type": "import",
            "source_id": "users",
            "narrative": "line one\nline two\nline three",
        },
        {
            "id": "tst",
            "type": "test",
            "inputs": ["imp"],
            "config": {
                "logic": "all",
                "severity": "high",
                "item_key_column": "user_id",
                "description_template": "User {user_id}",
                "conditions": [{"column": "can_create", "op": "eq", "value": True}],
            },
        },
    ]
    client.post(
        "/controls/c1/logic/builder",
        data={"pipeline_json": json.dumps({"nodes": nodes})},
        follow_redirects=False,
    )
    page = client.get("/controls/c1/logic/builder")
    assert "line one" in page.text and "line three" in page.text
