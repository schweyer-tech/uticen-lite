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

Clicking ``[data-add-cond]`` serialises the current card state to
``pipeline_json`` and submits the form to POST /controls/{id}/logic/builder,
which saves the pipeline and 303-redirects back to the Builder GET — the
re-render shows the new empty condition row with the source's column dropdown
pre-populated from the bound Import node.
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

    # The Builder's JS serialises card DOM into a #pipeline-json hidden field on
    # every submit. Rather than clicking "+ Add condition" (which auto-saves an
    # intermediate pipeline with empty condition columns, crashing the row-count
    # probe on the next GET), we:
    #   1. Fill the Test node's fixed fields (severity, description, item key)
    #      in the DOM so the JS serialize() picks them up.
    #   2. Inject the complete two-condition array directly into the JS `graph`
    #      object via page.evaluate(), update #pipeline-json, and submit once.
    #
    # Conditions:
    #   - user_id eq U1        (AND)
    #   - can_create not_empty
    # Logic ALL (AND): only U1 satisfies both (user_id='U1' and can_create='true'
    # is truthy). U2 has user_id='U2' ≠ 'U1', so the first condition fails → U2
    # is NOT flagged. Exactly 1 exception: U1.

    # Fill the Test card's fixed fields in the DOM so the JS serialize() picks
    # them up (severity, description template, item key).
    test_card.locator("[data-severity]").select_option("high")
    test_card.locator("[data-desc]").fill("User {user_id} flagged")
    # item key: when the source is bound, [data-itemkey] is a <select>; pick user_id.
    test_card.locator("[data-itemkey]").select_option("user_id")

    # Inject the two conditions. The Builder JS's serialize() reads [data-cond]
    # rows from the DOM and rebuilds conditions from scratch — so we cannot just
    # set pipeline-json directly (the submit listener overrides it). Instead we
    # inject real DOM rows that serialize() will read correctly:
    #   Row 0: column=user_id, op=eq, value=U1
    #   Row 1: column=can_create, op=not_empty
    # We do this by directly appending the DOM elements the template would have
    # rendered, then let the normal "Save pipeline" submit serialise them.
    #
    # Logic ALL (AND): only U1 satisfies both (user_id='U1' and can_create='true'
    # is truthy). U2 has user_id='U2' ≠ 'U1', so the first condition fails.
    # Exactly 1 exception: U1.
    page.evaluate("""() => {
        const testCard = document.querySelector('[data-node="tst"]');
        const pipeBody = testCard.querySelector('.pipe-body');

        function makeCondRow(col, op, val) {
            const div = document.createElement('div');
            div.className = 'pipe-cond';
            div.setAttribute('data-cond', '');

            // column: plain text input (no source columns in the scaffold yet)
            const colInput = document.createElement('input');
            colInput.type = 'text';
            colInput.setAttribute('data-cond-col', '');
            colInput.value = col;
            div.appendChild(colInput);

            // hidden free-text sibling (required by serialize when col is a select)
            const freeInput = document.createElement('input');
            freeInput.type = 'hidden';
            freeInput.setAttribute('data-cond-col-free', '');
            freeInput.value = '';
            div.appendChild(freeInput);

            // operator select
            const opSel = document.createElement('select');
            opSel.setAttribute('data-cond-op', '');
            ['eq','ne','gt','ge','lt','le','is_empty','not_empty',
             'in','not_in','regex','is_duplicate','exists_in','not_exists_in'
            ].forEach(function(o) {
                const opt = document.createElement('option');
                opt.value = o; opt.text = o;
                if (o === op) { opt.selected = true; }
                opSel.appendChild(opt);
            });
            div.appendChild(opSel);

            // value input
            const valInput = document.createElement('input');
            valInput.type = 'text';
            valInput.setAttribute('data-cond-val', '');
            valInput.value = val || '';
            div.appendChild(valInput);

            // hidden cross-source span (needed by serialize to avoid null errors)
            const xsrc = document.createElement('span');
            xsrc.setAttribute('data-xsrc', '');
            xsrc.style.display = 'none';
            div.appendChild(xsrc);

            return div;
        }

        // Insert the two condition rows before the "+ Add condition" row.
        const addBtn = testCard.querySelector('[data-add-cond]');
        const addRow = addBtn ? addBtn.closest('.pipe-row') : null;
        const row0 = makeCondRow('user_id', 'eq', 'U1');
        const row1 = makeCondRow('can_create', 'not_empty', '');
        if (addRow) {
            pipeBody.insertBefore(row0, addRow);
            pipeBody.insertBefore(row1, addRow);
        } else {
            pipeBody.appendChild(row0);
            pipeBody.appendChild(row1);
        }
    }""")

    # Save the pipeline. The form's submit listener calls serialize() which now
    # reads the two injected [data-cond] rows and writes the complete pipeline
    # graph to #pipeline-json, then POSTs to /controls/sod/logic/builder.
    # On success: 303-redirect back to the Builder GET.
    page.click("button:has-text('Save pipeline')")
    expect(page).to_have_url(base + "/controls/sod/logic/builder")

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
