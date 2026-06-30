"""Browser smoke: type a key on Logic ▸ Trace → walk + verdict render (issue #29).

Run via: pytest tests/e2e -m browser   (after: playwright install chromium)
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser

_CSV = b"invoice_id,amount\nINV001,100\nINV005,500\n"


def _seed(page: Page, base: str) -> str:
    page.request.post(
        f"{base}/sources",
        multipart={
            "source_id": "inv_tr",
            "format": "csv",
            "file": {"name": "inv_tr.csv", "mimeType": "text/csv", "buffer": _CSV},
        },
    )
    # Mark invoice_id as the key column (uploads default to no key).
    page.request.post(
        f"{base}/sources/inv_tr",
        form={
            "key_columns": "invoice_id",
            "data_type__invoice_id": "text",
            "data_type__amount": "number",
            "include__invoice_id": "on",
            "include__amount": "on",
        },
    )
    page.request.post(
        f"{base}/controls",
        form={
            "id": "tr_ctrl",
            "title": "Trace smoke",
            "objective": "o",
            "narrative": "n",
            "source_ids": "inv_tr",
            "failure_threshold_count": "0",
        },
    )
    graph = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "inv_tr"},
            {
                "id": "tst",
                "type": "test",
                "inputs": ["imp"],
                "config": {
                    "logic": "all",
                    "item_key_column": "invoice_id",
                    "conditions": [{"column": "amount", "op": "gt", "value": 100}],
                },
            },
        ]
    }
    page.request.post(
        f"{base}/controls/tr_ctrl/logic/builder",
        form={"pipeline_json": json.dumps(graph)},
    )
    return "tr_ctrl"


@pytest.mark.browser
def test_trace_tab_flags_a_record(page: Page, live_server: str) -> None:
    base = live_server
    cid = _seed(page, base)

    page.goto(f"{base}/controls/{cid}/logic/trace")
    # The Trace tab is present and active.
    expect(page.get_by_role("link", name="Trace")).to_be_visible()

    # Type a flagged key and submit.
    page.get_by_label("Item key to trace").fill("INV005")
    page.get_by_role("button", name="Trace").click()

    # The verdict + a per-condition row render.
    expect(page.get_by_text("Flagged as an exception", exact=False)).to_be_visible()
    expect(page.get_by_text("matched", exact=False).first).to_be_visible()
