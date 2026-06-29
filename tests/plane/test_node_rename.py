"""The Builder lets a user rename a node (give it a readable title); the title
persists and shows on the card + flowchart. 2026-06-27 review."""
import io
import json


def _seed(client):
    csv = b"user_id,can_create\nU1,true\n"
    client.post("/sources", data={"source_id": "users", "format": "csv"},
                files={"file": ("users.csv", io.BytesIO(csv), "text/csv")},
                follow_redirects=False)
    client.post("/controls", data={"id": "c1", "title": "C1", "objective": "o",
                "narrative": "n", "source_ids": ["users"], "failure_threshold_count": "0"},
                follow_redirects=False)


def _save(client, title_imp=""):
    graph = {"nodes": [
        {"id": "imp", "type": "import", "source_id": "users", "title": title_imp},
        {"id": "tst", "type": "test", "inputs": ["imp"], "title": "",
         "config": {"logic": "all", "severity": "high", "item_key_column": "user_id",
                    "description_template": "User {user_id}",
                    "conditions": [{"column": "can_create", "op": "eq", "value": True}]}},
    ]}
    return client.post("/controls/c1/logic/builder",
                       data={"pipeline_json": json.dumps(graph)}, follow_redirects=False)


def test_node_card_has_a_name_field(client):
    _seed(client)
    _save(client)
    page = client.get("/controls/c1/logic/builder")
    assert "data-node-title" in page.text


def test_rename_persists_and_shows_on_card(client):
    _seed(client)
    _save(client, title_imp="Access accounts")
    page = client.get("/controls/c1/logic/builder")
    assert "Access accounts" in page.text          # the readable title renders
    # and the title also labels the flowchart box
    fc = client.get("/controls/c1/logic/flowchart")
    assert "Access accounts" in fc.text
