"""View-model helper: render any control as a node graph for the Logic ▸ Builder tab.

Pure (no DB/IO). The graph it returns is the same shape parse_pipeline()/the Builder
template consume. Derived graphs compile back to the control's existing rule_spec, so a
derive→save round-trip is bundle-identical (cardinal rule 0001, learning 0010)."""
from __future__ import annotations

from typing import Any


def is_raw_python(control: dict[str, Any]) -> bool:
    return (
        bool(control.get("test_code"))
        and not control.get("pipeline")
        and not control.get("rule_spec")
    )


def derive_builder_graph(
    control: dict[str, Any], bound_source_ids: list[str]
) -> dict[str, Any] | None:
    if control.get("pipeline"):
        return control["pipeline"]
    if is_raw_python(control):
        return None
    primary = bound_source_ids[0] if bound_source_ids else None
    rule = control.get("rule_spec")
    conditions = list(rule["conditions"]) if rule else []
    test_cfg: dict[str, Any] = {
        "logic": (rule or {}).get("logic", "all"),
        "severity": (rule or {}).get("severity", "medium"),
        "item_key_column": (rule or {}).get("item_key_column"),
        "description_template": (rule or {}).get("description_template", ""),
        "conditions": conditions,
    }
    return {
        "nodes": [
            {"id": "src", "type": "import", "source_id": primary, "narrative": ""},
            {
                "id": "tst",
                "type": "test",
                "inputs": ["src"],
                "narrative": "",
                "config": test_cfg,
            },
        ]
    }
