"""End-to-end browser smoke test for the control plane (issue #13).

Drives a *live* ``controlplane`` through the real authoring flow with a real
browser (Chromium via pytest-playwright): upload + map a CSV source → author a
no-code rule control via the Logic ▸ Builder → run → assert the run view →
export the bundle → assert it validates against ``contract/bundle.schema.json``.
This guards the rendered UI end-to-end (the multi-run ordering regression in
PR #5 was caught only by human review). The export assertion IS the cardinal-
rule-0001 contract guard.

Excluded from the fast unit lane (``addopts = "--ignore=tests/e2e"``); run in CI
via ``pytest tests/e2e -m browser`` after ``playwright install chromium``.

Every selector below was grounded against the actual HTML rendered by the live
app (the Builder node cards use ``data-*`` attributes, not ``name`` fields, for
the JS-serialised graph; the selectors are scoped to ``[data-node="<id>"]`` so
Import and Test cards don't collide):

- Import card: ``[data-node="src"] [data-source]`` — the source ``<select>``
- Test card:   ``[data-node="tst"] [data-cond]`` — each condition row
  - column:    ``[data-cond-col]``  (<select> when source is bound, <input> otherwise)
  - operator:  ``[data-cond-op]``   (<select>)
  - value:     ``[data-cond-val]``  (<input type=text>)
  - add-cond:  ``[data-add-cond]``  (button inside the Test card)
  - severity:  ``[data-node="tst"] [data-severity]``
  - description: ``[data-node="tst"] [data-desc]``
  - item key:  ``[data-node="tst"] [data-itemkey]``
- Save:        ``button[type=submit]`` (text "Save pipeline")

Clicking ``[data-add-cond]`` serialises the current card state via JS
``serialize()`` (reading all [data-cond] rows, [data-severity], [data-desc],
[data-itemkey] from the DOM), writes ``pipeline_json``, and sends a fetch POST
with ``autosave=1`` to POST /controls/{id}/logic/builder. The response contains
only the updated cards fragment, which is swapped into the DOM in-place (scroll
position preserved) — no full-page navigation. The re-render shows the new
empty condition row with the source's column dropdown pre-populated from the
bound Import node.

The authoring sequence for the two-condition rule is therefore:
  1. Set severity/desc/itemkey on the initial 0-condition scaffold.
  2. Click ``[data-add-cond]`` — autosave → cards fragment swapped in-place, new
     empty condition row rendered.
  3. Fill condition row 0: select column=user_id, op=eq, value=U1.
  4. Click ``[data-add-cond]`` again — autosave → cards fragment swapped,
     condition row 0 preserved and new empty condition row 1 rendered.
  5. Fill condition row 1: select column=can_create, op=not_empty.
  6. Click "Save pipeline" — final save (full-page POST), 303-redirect, pipeline stored.
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

    # 3. Author a rule control via the Logic ▸ Builder.
    #
    #    Step A — create the control (Definition). GET /controls/new renders the
    #    metadata fields and the data-source checkbox list (no rule builder —
    #    Definition is now metadata-only). Bind the 'users' source so
    #    derive_builder_graph picks it as the Import node's source_id.
    page.goto(base + "/controls/new")
    page.fill("#f-id", "sod")
    page.fill("#f-title", "Segregation of duties")
    page.fill("#f-cnt", "0")
    page.check("input[name='source_ids'][value='users']")
    page.click("button[type=submit]")  # POST /controls → 303 /controls/sod
    expect(page).to_have_url(base + "/controls/sod")

    #    Step B — author logic in the Builder. GET /controls/sod/logic/builder
    #    renders the derived Import→Test scaffold: node "src" (Import, source
    #    already set to 'users') and node "tst" (Test, 0 conditions, severity
    #    'medium'). Selectors are scoped to [data-node="<id>"] so the Import and
    #    Test cards don't collide.
    page.goto(base + "/controls/sod/logic/builder")

    # The Import node's source should already be 'users' from the bound source_ids.
    # Assert it as a sanity check before touching the Test node.
    import_card = page.locator('[data-node="src"]')
    expect(import_card.locator("[data-source]")).to_have_value("users")

    test_card = page.locator('[data-node="tst"]')

    # Author the two-condition rule via the REAL Builder UI.
    #
    # Conditions:
    #   - user_id eq U1        (AND)
    #   - can_create not_empty
    # Logic ALL (AND): only U1 satisfies both (user_id='U1' and can_create='true'
    # is truthy). U2 has user_id='U2' ≠ 'U1', so the first condition fails → U2
    # is NOT flagged. Exactly 1 exception: U1.
    #
    # The scaffold starts with 0 conditions. Each "[data-add-cond]" click serialises
    # the current card DOM (via JS serialize()), appends an empty condition to the
    # in-memory graph, writes #pipeline-json, and submits the form. The server saves
    # the pipeline and 303-redirects to the GET, which re-renders the Test node with
    # the new empty condition row and the source's column <select> pre-populated.
    # We therefore set severity/desc/itemkey before the first click so they are
    # saved in the initial POST; the re-renders restore them from the stored graph.

    # --- Step 1: set fixed fields on the initial 0-condition scaffold, then add
    #             condition 0 (saves scaffold fields + adds empty condition row). ---
    test_card.locator("[data-severity]").select_option("high")
    test_card.locator("[data-desc]").fill("User {user_id} flagged")
    # item key: source is bound so [data-itemkey] is a <select>; pick user_id.
    test_card.locator("[data-itemkey]").select_option("user_id")
    # Click "+ Add condition" — JS serialises the card, adds an empty condition,
    # and autosaves via fetch (scroll-stable, in-place DOM swap). Wait for the
    # autosave response to update #pipe-cards.
    test_card.locator("[data-add-cond]").click()
    page.wait_for_load_state("networkidle")

    # --- Step 2: condition row 0 is now rendered with a column <select> (source
    #             'users' is bound so the server pre-populates it). Fill it:
    #             column=user_id, op=eq, value=U1. Then add condition 1. ---
    test_card = page.locator('[data-node="tst"]')
    cond_rows = test_card.locator("[data-cond]")
    row0 = cond_rows.nth(0)
    row0.locator("[data-cond-col]").select_option("user_id")
    row0.locator("[data-cond-op]").select_option("eq")
    row0.locator("[data-cond-val]").fill("U1")
    # Click "+ Add condition" again — saves condition 0 + appends empty condition 1.
    # Autosave via fetch (scroll-stable). Wait for networkidle to ensure response.
    test_card.locator("[data-add-cond]").click()
    page.wait_for_load_state("networkidle")

    # --- Step 3: condition row 1 is now rendered. Fill it:
    #             column=can_create, op=not_empty (no value needed). ---
    test_card = page.locator('[data-node="tst"]')
    cond_rows = test_card.locator("[data-cond]")
    row1 = cond_rows.nth(1)
    row1.locator("[data-cond-col]").select_option("can_create")
    row1.locator("[data-cond-op]").select_option("not_empty")

    # --- Step 3b: Add a procedure SECTION. Click "＋ Add procedure" (#proc-add) to
    #              append a collapsible <details> section whose header IS the
    #              procedure editor; the JS injects its <option> into the Test card's
    #              "Belongs to" select immediately (no server round-trip, so the
    #              filled condition rows above are untouched). Fill the header, then
    #              assign the Test to it — selecting re-groups the Test under the new
    #              section via a scroll-stable autosave (serializeProcedures() reads
    #              the section headers on every serialize()).
    page.click("#proc-add")
    new_section = page.locator("[data-proc-section]").last
    pid = new_section.get_attribute("data-band-key")
    assert pid
    new_section.locator("[data-proc-code]").fill("P1")
    new_section.locator("[data-proc-name]").fill("Manual JE Review")
    new_section.locator("[data-proc-assert]").fill("Segregation of Duties")
    page.locator('[data-node="tst"] [data-procedure]').select_option(pid)
    page.wait_for_load_state("networkidle")  # change → autosave re-groups the card

    # --- Step 4: save the final pipeline. The form's submit listener calls
    #             serialize() (which reads both [data-cond] rows, the fixed fields,
    #             the Test's procedure_id, AND every procedure SECTION header), writes
    #             #pipeline-json, and POSTs to the builder endpoint. On success:
    #             303-redirect back to the Builder GET. ---
    page.click("button:has-text('Save pipeline')")
    expect(page).to_have_url(base + "/controls/sod/logic/builder")

    # --- Step 3c: reload the Builder and assert the procedure round-tripped: the
    #              section header re-hydrates with its name, and the Test's "Belongs to"
    #              still selects the procedure we created (effective-owner preselect). ---
    page.goto(base + "/controls/sod/logic/builder")
    expect(
        page.locator(f'[data-proc-head][data-proc-id="{pid}"] [data-proc-name]')
    ).to_have_value("Manual JE Review")
    expect(page.locator('[data-node="tst"] [data-procedure]')).to_have_value(pid)

    # 4. Run it. The run button lives on the dashboard as a row-scoped
    #    <form action="/controls/sod/run"> with a "Run" submit button.
    #    POST /controls/sod/run 303-redirects to the run view.
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


@pytest.mark.browser
def test_add_source_has_upload_and_url_modes(page: Page, live_server: str) -> None:
    """Smoke-check that the add-source page shows both modes and the URL form's
    secrets warning (learning 0012 — add-source form restructured in place with
    upload/URL mode toggle + sheet field).
    """
    page.goto(f"{live_server}/sources/new")
    assert page.locator("text=Upload file").count() >= 1
    assert page.locator("text=Fetch from URL").count() >= 1
    page.goto(f"{live_server}/sources/from-url")
    assert "plaintext" in page.content().lower()
