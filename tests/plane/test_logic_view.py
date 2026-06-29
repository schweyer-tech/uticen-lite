from uticen_lite.pipeline.compile import compile_pipeline
from uticen_lite.pipeline.model import parse_pipeline
from uticen_lite.plane.logic_view import derive_builder_graph, is_raw_python


def test_stored_pipeline_is_returned_verbatim():
    g = {"nodes": [{"id": "a", "type": "import", "source_id": "s"}]}
    assert derive_builder_graph({"pipeline": g}, ["s"]) is g


def test_single_source_rule_spec_becomes_import_then_test():
    rule = {
        "logic": "all",
        "severity": "high",
        "item_key_column": "id",
        "description_template": "x {id}",
        "conditions": [{"column": "mfa", "op": "eq", "value": False}],
    }
    g = derive_builder_graph({"rule_spec": rule, "source_ids": ["acc"]}, ["acc"])
    assert [n["type"] for n in g["nodes"]] == ["import", "test"]
    imp, test = g["nodes"]
    assert imp["source_id"] == "acc"
    assert test["inputs"] == [imp["id"]]
    assert test["config"]["conditions"] == rule["conditions"]
    assert test["config"]["severity"] == "high"
    assert test["config"]["item_key_column"] == "id"
    assert test["config"]["description_template"] == rule["description_template"]


def test_cross_source_rule_keeps_condition_on_test_node():
    rule = {
        "logic": "all",
        "severity": "high",
        "item_key_column": "pid",
        "description_template": "x",
        "conditions": [
            {
                "op": "not_exists_in",
                "column": "vendor_id",
                "other_source": "vmaster",
                "this_key": "vendor_id",
                "other_key": "vendor_id",
            }
        ],
    }
    g = derive_builder_graph(
        {"rule_spec": rule, "source_ids": ["pay", "vmaster"]}, ["pay", "vmaster"]
    )
    assert [n["type"] for n in g["nodes"]] == ["import", "test"]
    assert g["nodes"][0]["source_id"] == "pay"  # primary
    assert g["nodes"][1]["config"]["conditions"] == rule["conditions"]


def test_empty_control_yields_scaffold():
    g = derive_builder_graph({"source_ids": ["acc"]}, ["acc"])
    assert [n["type"] for n in g["nodes"]] == ["import", "test"]
    assert g["nodes"][0]["source_id"] == "acc"
    assert g["nodes"][1]["config"]["conditions"] == []


def test_empty_control_no_sources_scaffold_has_unbound_import():
    g = derive_builder_graph({"source_ids": []}, [])
    assert g["nodes"][0]["type"] == "import"
    assert g["nodes"][0].get("source_id") in (None, "")


def test_raw_python_returns_none():
    assert (
        derive_builder_graph({"test_code": "def test(pop):\n    return []"}, [])
        is None
    )
    assert is_raw_python({"test_code": "def test(pop): ..."}) is True
    assert is_raw_python({"rule_spec": {"conditions": []}}) is False


def test_cross_source_rule_round_trips_to_same_rule_spec():
    rule = {
        "logic": "all",
        "severity": "high",
        "item_key_column": "pid",
        "description_template": "x",
        "conditions": [
            {
                "op": "not_exists_in",
                "column": "vendor_id",
                "other_source": "vmaster",
                "this_key": "vendor_id",
                "other_key": "vendor_id",
            }
        ],
    }
    g = derive_builder_graph(
        {"rule_spec": rule, "source_ids": ["pay", "vmaster"]}, ["pay", "vmaster"]
    )
    compiled = compile_pipeline(parse_pipeline(g))
    assert compiled.rule_spec is not None
    assert compiled.rule_spec["conditions"] == rule["conditions"]


def test_exists_in_cross_source_rule_round_trips_to_same_rule_spec():
    """exists_in is symmetric to not_exists_in: derive→compile must preserve the
    condition (op, other_source, this_key, other_key) unchanged."""
    rule = {
        "logic": "all",
        "severity": "medium",
        "item_key_column": "invoice_id",
        "description_template": "Invoice {invoice_id} has no matching vendor",
        "conditions": [
            {
                "op": "exists_in",
                "column": "vendor_id",
                "other_source": "vendors",
                "this_key": "vendor_id",
                "other_key": "vendor_id",
            }
        ],
    }
    g = derive_builder_graph(
        {"rule_spec": rule, "source_ids": ["invoices", "vendors"]}, ["invoices", "vendors"]
    )
    assert [n["type"] for n in g["nodes"]] == ["import", "test"]
    # The derived Test node must carry the exists_in condition verbatim.
    test_node = g["nodes"][1]
    assert test_node["config"]["conditions"] == rule["conditions"]
    # Compile→parse round-trip must preserve the condition.
    compiled = compile_pipeline(parse_pipeline(g))
    assert compiled.rule_spec is not None
    assert compiled.rule_spec["conditions"] == rule["conditions"]
