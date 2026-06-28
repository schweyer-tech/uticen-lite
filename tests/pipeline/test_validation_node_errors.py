"""A non-terminal pipeline endpoint must report a node-id-prefixed error so the
editor can pin it on the offending card (2026-06-27 review)."""
import pytest

from controlflow_sdk.pipeline.model import PipelineError, parse_pipeline
from controlflow_sdk.plane.routes.pipeline import _node_errors_from


def test_terminal_error_is_node_prefixed():
    # A lone Import node feeds nothing and is not a Test → non-terminal sink.
    graph = {"nodes": [{"id": "imp_access_accounts", "type": "import", "source_id": "users"}]}
    with pytest.raises(PipelineError) as ei:
        parse_pipeline(graph)
    assert str(ei.value).startswith("node 'imp_access_accounts': ")


def test_node_errors_from_maps_the_terminal_error():
    graph = {"nodes": [{"id": "imp_access_accounts", "type": "import", "source_id": "users"}]}
    try:
        parse_pipeline(graph)
    except PipelineError as exc:
        per_node = _node_errors_from([str(exc)])
    assert "imp_access_accounts" in per_node
    assert per_node["imp_access_accounts"][0].startswith("must end in a Test")
