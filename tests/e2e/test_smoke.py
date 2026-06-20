"""End-to-end browser smoke test for the control plane (issue #13).

Drives a *live* ``controlplane`` through the real authoring flow with a real
browser (Chromium via pytest-playwright): upload + map a CSV source → author a
no-code rule control → run → assert the run view → export the bundle → assert it
validates against ``contract/bundle.schema.json``. This guards the rendered UI
end-to-end (the multi-run ordering regression in PR #5 was caught only by human
review). The export assertion IS the cardinal-rule-0001 contract guard.

Excluded from the fast unit lane (``addopts = "--ignore=tests/e2e"``); run in CI
via ``pytest tests/e2e -m browser`` after ``playwright install chromium``.

Every selector below was grounded against the actual HTML rendered by the live
app via the FastAPI TestClient (the #9 work made the condition COLUMN field a
server-rendered ``<select>`` for a bound source, with a free-text fallback when
none is bound — both surfaces are exercised here).
"""

import json
import re
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from controlflow_sdk.schema.validate import validate_bundle

# Two rows. The deterministic outcome is engineered below so exactly U1 is
# flagged and U2 passes (see the rule conditions in the test).
CSV = b"user_id,can_create,can_approve\nU1,true,true\nU2,true,false\n"


@pytest.mark.browser
def test_author_run_export_smoke(page: Page, live_server: str, tmp_path: Path) -> None:
    base = live_server

    # 1. Dashboard renders. The named engagement skips the first-run setup
    #    screen and shows the Controls page (h1 "Controls"); the engagement name
    #    appears in the header chip.
    page.goto(base + "/")
    expect(page.get_by_role("heading", name="Controls", exact=True)).to_be_visible()
    expect(page.locator(".chip")).to_have_text("E2E Co")

    # 2. Upload a CSV source. GET /sources/new (source_new.html) has the
    #    multipart form: #s-id (name=source_id), file input #s-file (name=file),
    #    #s-asof (name=as_of_date, required). POST /sources 303-redirects to the
    #    source Definition tab /sources/users.
    page.goto(base + "/sources/new")
    page.fill("#s-id", "users")
    page.set_input_files(
        "#s-file",
        files=[{"name": "users.csv", "mimeType": "text/csv", "buffer": CSV}],
    )
    page.fill("#s-asof", "2026-01-31")
    page.click("button[type=submit]")
    expect(page).to_have_url(base + "/sources/users")

    # 3. Author a rule control. GET /controls/new (control_edit.html) renders the
    #    Details fields, the source checkbox list, and the rule builder. The
    #    FIRST condition row is a free-text input[name=cond_column] because no
    #    source is bound at form-render time.
    page.goto(base + "/controls/new")
    page.fill("#f-id", "sod")
    page.fill("#f-title", "Segregation of duties")
    page.fill("input[name='rule_description']", "User {user_id} flagged")
    page.fill("input[name='rule_item_key']", "user_id")
    page.select_option("select[name='rule_severity']", "high")
    # logic defaults to "all" (AND); fail on any exception.
    page.fill("#f-cnt", "0")

    # Condition 1 (free-text row): user_id eq U1.
    cond_columns = page.locator("[name='cond_column']")
    expect(cond_columns).to_have_count(1)
    cond_columns.first.fill("user_id")
    page.locator("select[name='cond_op']").first.select_option("eq")
    page.locator("input[name='cond_value']").first.fill("U1")

    # Bind the source BEFORE adding the second condition: the "+ Add condition"
    # button rewrites the htmx request with source_id = first checked source, so
    # the new row renders the #9 server-side COLUMN <select> dropdown.
    page.check("input[name='source_ids'][value='users']")

    # Condition 2 (dropdown row, injected by htmx): can_create not_empty.
    page.click("button:has-text('+ Add condition')")
    expect(cond_columns).to_have_count(2)
    # The second row's cond_column is a <select> (the #9 dropdown for the bound
    # source). select_option keys off the option value, confirming the dropdown
    # is the one in play.
    page.locator("select[name='cond_column']").select_option("can_create")
    page.locator("select[name='cond_op']").nth(1).select_option("not_empty")

    page.click("button[type=submit]")  # POST /controls -> 303 /controls/sod
    expect(page).to_have_url(base + "/controls/sod")

    # 4. Run it. control_edit.html has no Run button — the run lives on the
    #    dashboard as a row-scoped <form action="/controls/sod/run"> with a "Run"
    #    submit button. POST /controls/sod/run 303-redirects to the run view.
    page.goto(base + "/")
    page.click("form[action='/controls/sod/run'] button[type=submit]")
    expect(page).to_have_url(re.compile(r"/controls/sod/runs/"))

    # 5. Assert the run view (run_view.html): Records tested = 2, Failed = 1, the
    #    "Operated with deficiencies" pill, and exactly U1 in the exceptions
    #    table. The exceptions table is scoped explicitly: U2 also appears inside
    #    the workpaper <iframe> data preview (a separate frame), so we assert
    #    against the main-document exceptions table only.
    tiles = page.locator(".tile")
    expect(tiles.filter(has_text="Records tested").locator(".tile-value")).to_have_text("2")
    expect(tiles.filter(has_text="Failed").locator(".tile-value")).to_have_text("1")
    expect(page.get_by_text("Operated with deficiencies")).to_be_visible()

    exceptions_table = page.locator(".table-wrap table")
    # exact=True targets the item-key cell <td>U1</td>, not the description cell
    # "User U1 flagged" which also contains "U1".
    expect(exceptions_table.get_by_role("cell", name="U1", exact=True)).to_be_visible()
    expect(exceptions_table.get_by_role("cell", name="U2", exact=True)).to_have_count(0)

    # 6. Export the bundle and validate it against the contract. GET /export has
    #    a POST /export form whose submit returns the bundle.zip FileResponse.
    page.goto(base + "/export")
    with page.expect_download() as dl_info:
        page.click("form[action='/export'] button[type=submit]")
    out = tmp_path / "bundle.zip"
    dl_info.value.save_as(out)

    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["schema_version"] == "1.0"
    # THE contract guard (cardinal rule 0001): the exported bundle still passes
    # the same validator the ControlFlow app vendors.
    assert validate_bundle(manifest) == []
    assert any(c["id"] == "sod" for c in manifest["controls"])
