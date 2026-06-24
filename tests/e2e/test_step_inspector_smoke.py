"""Browser smoke: click a step count → step data opens in a new tab → table + links present.

Opt-in test (the ``browser`` marker is excluded from the fast unit lane via
``addopts = "--ignore=tests/e2e"`` in pyproject.toml).  CI runs it via:

    pytest tests/e2e -m browser

after ``playwright install chromium``.

Fixtures used:
- ``live_server`` (str base URL) — from ``tests/e2e/conftest.py``: a real
  uvicorn server on a free port, torn down after the test.
- ``page`` (playwright.sync_api.Page) — from pytest-playwright.

Seeding is done via Playwright's ``page.request.post`` against ``live_server``
(no separate test-client import needed — keeps fixture deps minimal).
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser

# Source CSV: two rows, so the Import step has row count 2 (shown as the
# clickable badge on the node card).
_CSV = b"user_id,can_create\nU1,true\nU2,false\n"


def _seed(page: Page, base: str) -> str:
    """POST source + control + pipeline + run via the live API; return the
    control id."""
    # Upload source.
    page.request.post(
        f"{base}/sources",
        multipart={
            "source_id": "users_insp",
            "format": "csv",
            "file": {
                "name": "users_insp.csv",
                "mimeType": "text/csv",
                "buffer": _CSV,
            },
        },
    )

    # Create control.
    page.request.post(
        f"{base}/controls",
        form={
            "id": "insp_ctrl",
            "title": "Inspector smoke",
            "objective": "o",
            "narrative": "n",
            "source_ids": "users_insp",
            "failure_threshold_count": "0",
        },
    )

    # Save a pipeline: Import(users_insp) → Filter → Test.
    # The Filter node is non-terminal, so its step count badge is what we click.
    graph = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "users_insp"},
            {
                "id": "flt",
                "type": "filter",
                "inputs": ["imp"],
                "config": {
                    "logic": "all",
                    "conditions": [{"column": "can_create", "op": "not_empty"}],
                },
            },
            {
                "id": "tst",
                "type": "test",
                "inputs": ["flt"],
                "config": {
                    "logic": "all",
                    "severity": "high",
                    "item_key_column": "user_id",
                    "description_template": "User {user_id}",
                    "conditions": [
                        {"column": "can_create", "op": "eq", "value": "true"}
                    ],
                },
            },
        ]
    }
    page.request.post(
        f"{base}/controls/insp_ctrl/logic/builder",
        form={"pipeline_json": json.dumps(graph)},
    )

    # Run the control so step row counts are computed and the inspector has data.
    page.request.post(f"{base}/controls/insp_ctrl/run")

    return "insp_ctrl"


@pytest.mark.browser
def test_step_inspector_opens_new_tab(page: Page, live_server: str) -> None:
    """Click a step count badge → it opens the step data as a full page in a NEW TAB
    with a table, the per-step download link, and a back-to-builder link."""
    base = live_server
    cid = _seed(page, base)

    # Navigate to the Logic ▸ Builder page for the seeded control.
    page.goto(f"{base}/controls/{cid}/logic/builder")

    # The pipe-count-btn is only rendered when node.count is not None.  It is now
    # an anchor that opens the step-data page in a new tab (target="_blank").
    count_link = page.locator(".pipe-count-btn").first
    expect(count_link).to_be_visible()
    expect(count_link).to_have_attribute("target", "_blank")

    # Clicking opens a new browser tab (popup); capture it.
    with page.context.expect_page() as popup_info:
        count_link.click()
    popup = popup_info.value
    popup.wait_for_load_state()

    # The new tab is the full step-data page: a data table, the per-step export
    # link, and a back-to-builder link.
    expect(popup.locator("table")).to_have_count(1)
    expect(popup.get_by_text("Download this step", exact=False)).to_have_count(1)
    expect(popup.get_by_text("Back to builder", exact=False)).to_have_count(1)
