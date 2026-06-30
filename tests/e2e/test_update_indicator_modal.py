"""Browser regression for the header update modal focus behavior."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from uticen_lite.store import repo
from uticen_lite.store.db import connect

pytestmark = pytest.mark.browser


def test_header_update_modal_traps_focus_and_restores_trigger(page, live_server):
    base = live_server
    page.route(
        "**/updates/indicator",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body="""
            <div id="header-update-indicator" class="update-indicator-wrap">
              <button type="button"
                      class="update-indicator update-available"
                      aria-label="Update available: 0.2.0"
                      title="Update available: 0.2.0"
                      aria-controls="update-modal"
                      data-update-modal-open>
                <span class="indicator-dot"></span>
                <span class="sr-only">Update available: 0.2.0</span>
              </button>
              <template id="update-modal-template">
                <div class="update-modal-body">
                  <h2 id="update-modal-title">Update available: 0.2.0</h2>
                  <p class="lead">Version 0.2.0 is available.</p>
                  <div class="page-actions">
                    <button type="button" class="btn btn-sm btn-primary">
                      Update now
                    </button>
                    <button type="button" class="btn btn-sm btn-ghost" data-update-modal-close>
                      Close
                    </button>
                  </div>
                </div>
              </template>
            </div>
            """,
        ),
    )

    page.goto(base + "/")
    assert "120000" in page.content()
    trigger = page.locator("[data-update-modal-open]")
    expect(trigger).to_be_visible()
    trigger.dispatch_event("click")

    modal = page.locator("#update-modal")
    expect(modal).to_be_visible()

    assert page.evaluate(
        "() => document.activeElement && "
        "document.activeElement.matches('[data-update-modal-close]')"
    )
    page.keyboard.press("Tab")
    assert page.evaluate(
        "() => document.activeElement && document.activeElement.textContent.trim() === 'Update now'"
    )
    page.keyboard.press("Tab")
    assert page.evaluate(
        "() => document.activeElement && "
        "document.activeElement.matches('[data-update-modal-close]')"
    )
    page.keyboard.press("Shift+Tab")
    assert page.evaluate(
        "() => document.activeElement && document.activeElement.textContent.trim() === 'Update now'"
    )

    modal.click(position={"x": 6, "y": 6})
    page.wait_for_function(
        "() => document.activeElement && "
        "document.activeElement.matches('[data-update-modal-open]')",
        timeout=2000,
    )


def test_header_update_indicator_stays_hidden_when_checks_are_off(page, live_server, engagement):
    # The launch check defaults ON, so explicitly turn it OFF to assert the
    # zero-egress path leaves the header indicator hidden.
    conn = connect(engagement)
    repo.set_check_updates_on_launch(conn, False)
    conn.close()
    base = live_server
    page.goto(base + "/")
    assert "120000" in page.content()
    assert page.locator("[data-update-modal-open]").count() == 0
