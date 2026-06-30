"""Nodes carry an optional human-readable ``title`` (rename). It round-trips
through parse and is excluded from the content-addressed step-cache key, so a
title-only edit never busts the cache (learning 0030; 2026-06-27 review)."""

from uticen_lite.pipeline.materialize import _step_keys
from uticen_lite.pipeline.model import parse_pipeline


def _graph(title_imp="", title_tst=""):
    return {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "users", "title": title_imp},
            {
                "id": "tst",
                "type": "test",
                "inputs": ["imp"],
                "title": title_tst,
                "config": {
                    "logic": "all",
                    "severity": "high",
                    "item_key_column": "user_id",
                    "description_template": "User {user_id}",
                    "conditions": [{"column": "can_create", "op": "eq", "value": True}],
                },
            },
        ]
    }


def test_title_round_trips_through_parse():
    p = parse_pipeline(_graph(title_imp="Access accounts"))
    assert p.node("imp").title == "Access accounts"
    # absent title defaults to empty
    assert p.node("tst").title == ""


def test_title_only_edit_does_not_bust_cache_key():
    before = _step_keys(parse_pipeline(_graph()), {"users": "v1"})
    after = _step_keys(
        parse_pipeline(_graph(title_imp="Renamed!", title_tst="Also renamed")), {"users": "v1"}
    )
    assert before == after  # cosmetic rename must not change any node's content hash


def test_data_change_still_busts_cache_key():
    before = _step_keys(parse_pipeline(_graph()), {"users": "v1"})
    g = _graph()
    g["nodes"][1]["config"]["conditions"][0]["value"] = False  # a real data change
    after = _step_keys(parse_pipeline(g), {"users": "v1"})
    assert before != after
