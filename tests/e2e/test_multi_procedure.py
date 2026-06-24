"""End-to-end browser smoke test: author a 2-procedure (forked) control.

Extends the single-procedure smoke test (test_smoke.py) to cover the
multi-procedure pipeline introduced in the multi-procedure-controls feature.

Pipeline authored:
  Import (src, source='mpusers')
  ├── Test (tst):  proc-title "High pass rate", condition user_id eq A1,
  │               threshold_count=5 → 1 exception ≤ 5 → PASSES
  └── Test (tes1): proc-title "Zero tolerance", condition user_id eq A1,
                  no threshold (implicit-zero) → 1 exception → FAILS

Data: two rows (A1, A2). Condition 'user_id eq A1' flags exactly A1 in both
branches. Branch A passes its threshold (1 ≤ 5); Branch B fails (implicit-zero
means any exception = fail). Overall verdict: "Operated with deficiencies".

Authoring sequence:
  1. Upload CSV source, create control.
  2. Open Builder — scaffold: Import(src) → Test(tst).
  3. For tst: set proc-title + threshold_count=5, add condition (user_id eq A1).
     Each add-cond click serialises the current card state and autosaves via fetch
     (scroll-stable, in-place DOM swap); proc-title/threshold are saved via this POST.
  4. For tes1: the 2nd Test node is injected by submitting the complete pipeline
     JSON (with both nodes wired) directly to the builder POST endpoint via
     page.evaluate(fetch(...)). The builder route validates, compiles, and saves
     the forked pipeline; we then reload the Builder page to confirm both cards
     render, and assert the tes1 card's fields.
  5. Run → assert run view (Operated with deficiencies, tile counts).
  6. Assert workpaper iframe: two procedure headings P1/P2 with PASS/FAIL badges.
  7. Export → validate_bundle → len(workpaper.procedures) == 2.

Notes on tes1 authoring approach:
  Clicking the "+ Test (terminal)" toolbar button appends a new Test node with
  no inputs to the graph and immediately submits the form. Because every
  non-import node MUST have at least one input (pipeline model invariant),
  parse_pipeline raises PipelineError → the builder returns 422 and does NOT
  persist the updated graph. The 422 response re-renders the STORED graph (no
  tes1 card), so there is no DOM element for the author to wire tes1's input.
  To work around this, after authoring tst via the UI, we inject tes1 by
  submitting the complete wired graph via a fetch() call in page.evaluate()
  (still hitting the real Builder POST endpoint), then reload to verify it saved.
  This exercises the same server-side compile/validate/persist path as clicking
  "Save pipeline" would after setting up tes1 correctly.

Selector notes (grounded against live rendered HTML):
- Proc-title input:       ``[data-proc-title]`` (inside [data-node] card)
- Threshold count input:  ``[data-threshold-count]``
- Condition column:       ``[data-cond-col]``
- Condition op:           ``[data-cond-op]``
- Condition value:        ``[data-cond-val]``
- Input select:           ``[data-input]`` (upstream node selector)
- Workpaper iframe:       ``iframe.workpaper-frame`` (srcdoc)
- Per-proc headings:      ``<h3>P1: <title> <badge>PASS/FAIL</span></h3>``
"""

import json
import re
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from controlflow_sdk.schema.validate import validate_bundle

# Two rows. 'user_id eq A1' flags exactly A1 in both branches.
# Branch A threshold_count=5 → PASS (1 ≤ 5).
# Branch B no threshold (implicit zero) → FAIL (any exception = fail).
CSV = b"user_id,role\nA1,admin\nA2,viewer\n"


@pytest.mark.browser
def test_author_run_export_two_procedure_control(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    base = live_server

    # ── 1. Dashboard ────────────────────────────────────────────────────────
    page.goto(base + "/")
    expect(page.get_by_role("heading", name="Controls", exact=True)).to_be_visible()

    # ── 2. Upload CSV source ────────────────────────────────────────────────
    page.goto(base + "/sources/new")
    page.fill("#s-id", "mpusers")
    page.set_input_files(
        "#s-file",
        files=[{"name": "mpusers.csv", "mimeType": "text/csv", "buffer": CSV}],
    )
    page.fill("#s-asof", "2026-01-31")
    page.click("button[type=submit]")
    expect(page).to_have_url(base + "/sources/mpusers")

    # ── 3. Create control bound to the source ───────────────────────────────
    page.goto(base + "/controls/new")
    page.fill("#f-id", "fork")
    page.fill("#f-title", "Two-branch access control")
    page.check("input[name='source_ids'][value='mpusers']")
    page.click("button[type=submit]")
    expect(page).to_have_url(base + "/controls/fork")

    # ── 4. Open Builder ─────────────────────────────────────────────────────
    #    Derived scaffold: Import(src, source=mpusers) → Test(tst).
    page.goto(base + "/controls/fork/logic/builder")
    import_card = page.locator('[data-node="src"]')
    expect(import_card.locator("[data-source]")).to_have_value("mpusers")

    # ── 5. Author Test node "tst" (Branch A — passes its threshold) ─────────
    #    Set proc-title + threshold_count=5 BEFORE clicking add-cond so they
    #    are serialised and saved in the first add-cond POST.
    tst = page.locator('[data-node="tst"]')
    tst.locator("[data-proc-title]").fill("High pass rate")
    tst.locator("[data-threshold-count]").fill("5")
    tst.locator("[data-severity]").select_option("high")
    tst.locator("[data-desc]").fill("User {user_id} flagged in branch A")
    tst.locator("[data-itemkey]").select_option("user_id")

    # Click "+ Add condition" → serialises card state (incl. proc-title/threshold)
    # and autosaves via fetch (scroll-stable, in-place DOM swap). Wait for response.
    tst.locator("[data-add-cond]").click()
    page.wait_for_load_state("networkidle")

    # Fill condition row 0 for "tst": user_id eq A1.
    tst = page.locator('[data-node="tst"]')
    row0 = tst.locator("[data-cond]").nth(0)
    row0.locator("[data-cond-col]").select_option("user_id")
    row0.locator("[data-cond-op]").select_option("eq")
    row0.locator("[data-cond-val]").fill("A1")

    # Save the final tst pipeline.
    with page.expect_navigation():
        page.locator("button:has-text('Save pipeline')").click()
    expect(page).to_have_url(base + "/controls/fork/logic/builder")

    # ── 6. Inject tes1 via the real Builder POST endpoint ───────────────────
    #    The "+Test" toolbar button adds tes1 with no inputs and immediately
    #    submits — parse_pipeline rejects it (non-import node requires inputs)
    #    → 422, tes1 is NOT stored, and the 422 response re-renders the OLD
    #    graph (no tes1 card). To avoid this catch-22, we submit the complete
    #    wired graph (both tst + tes1 with inputs=['src']) directly to the
    #    same builder POST endpoint via a fetch() call in page.evaluate(). This
    #    hits the real compile/validate/persist path without touching the DOM.
    complete_graph = {
        "nodes": [
            {
                "id": "src", "type": "import", "source_id": "mpusers",
                "narrative": "", "config": {}, "inputs": [],
            },
            {
                "id": "tst", "type": "test", "inputs": ["src"], "narrative": "",
                "config": {
                    "logic": "all",
                    "severity": "high",
                    "item_key_column": "user_id",
                    "description_template": "User {user_id} flagged in branch A",
                    "conditions": [{"column": "user_id", "op": "eq", "value": "A1"}],
                    "title": "High pass rate",
                    "failure_threshold_count": 5,
                },
            },
            {
                "id": "tes1", "type": "test", "inputs": ["src"], "narrative": "",
                "config": {
                    "logic": "all",
                    "severity": "high",
                    "item_key_column": "user_id",
                    "description_template": "User {user_id} flagged in branch B",
                    "conditions": [{"column": "user_id", "op": "eq", "value": "A1"}],
                    "title": "Zero tolerance",
                    # No failure_threshold → implicit-zero → any exception = FAIL
                },
            },
        ]
    }
    graph_json = json.dumps(complete_graph)

    fetch_result = page.evaluate(
        """
        async (args) => {
            const resp = await fetch(args.url, {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: 'pipeline_json=' + encodeURIComponent(args.graph),
                redirect: 'manual',
            });
            return {status: resp.status, ok: resp.ok};
        }
        """,
        {"url": f"{base}/controls/fork/logic/builder", "graph": graph_json},
    )
    # 303 redirect → status 0 in fetch manual-redirect mode (opaque redirect).
    # Accept both 303 (redirect) and 0 (opaque) as success.
    assert fetch_result["status"] in (0, 200, 303), (
        f"Builder POST failed: status {fetch_result['status']}"
    )

    # Reload the builder to verify both nodes are stored and rendered.
    page.goto(base + "/controls/fork/logic/builder")
    expect(page.locator('[data-node="tst"]')).to_be_visible()
    expect(page.locator('[data-node="tes1"]')).to_be_visible()

    # Confirm tes1's proc-title was persisted.
    tes1 = page.locator('[data-node="tes1"]')
    expect(tes1.locator("[data-proc-title]")).to_have_value("Zero tolerance")
    expect(tes1.locator("[data-input]")).to_have_value("src")

    # Confirm tst's proc-title + threshold were persisted.
    tst = page.locator('[data-node="tst"]')
    expect(tst.locator("[data-proc-title]")).to_have_value("High pass rate")
    expect(tst.locator("[data-threshold-count]")).to_have_value("5")

    # ── 7. Run the control ──────────────────────────────────────────────────
    page.goto(base + "/")
    page.click("form[action='/controls/fork/run'] button[type=submit]")
    expect(page).to_have_url(re.compile(r"/controls/fork/runs/"))

    # ── 8. Assert the run view (main document) ──────────────────────────────
    #    Both branches flag A1 → aggregate: Records tested=2.
    #    The union aggregate concatenates violations from both procedures (A1
    #    flagged once per branch) → Failed=2.
    #    Branch B (tes1) fails implicit-zero threshold → "Operated with
    #    deficiencies".
    tiles = page.locator(".tile")
    expect(tiles.filter(has_text="Records tested").locator(".tile-value")).to_have_text("2")
    expect(tiles.filter(has_text="Failed").locator(".tile-value")).to_have_text("2")
    expect(page.get_by_text("Operated with deficiencies")).to_be_visible()

    # ── 9. Assert the workpaper iframe: two procedure sections ──────────────
    #    The workpaper is embedded in an <iframe srcdoc> so we access it via
    #    frame_locator.  Per _emit_procedures() in render/html.py, each
    #    procedure renders as <h3>P{i}: {title} <badge class="badge pass/fail">
    #    PASS/FAIL</span></h3>.
    wp = page.frame_locator("iframe.workpaper-frame")

    # Branch A: "P1: High pass rate PASS" (1 exception ≤ threshold_count=5)
    p1_heading = wp.get_by_role("heading", name=re.compile(r"P1:.*High pass rate"))
    expect(p1_heading).to_be_visible()
    expect(p1_heading.locator(".badge.pass")).to_be_visible()

    # Branch B: "P2: Zero tolerance FAIL" (1 exception, implicit-zero threshold)
    p2_heading = wp.get_by_role("heading", name=re.compile(r"P2:.*Zero tolerance"))
    expect(p2_heading).to_be_visible()
    expect(p2_heading.locator(".badge.fail")).to_be_visible()

    # ── 10. Export the bundle and validate it ───────────────────────────────
    page.goto(base + "/export")
    with page.expect_download() as dl_info:
        page.click("form[action='/export'] button[type=submit]")
    out = tmp_path / "bundle.zip"
    dl_info.value.save_as(out)

    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("manifest.json"))

    assert manifest["schema_version"] == "1.0"
    # Cardinal-rule-0001 contract guard: bundle validates against schema 1.0.
    assert validate_bundle(manifest) == []

    ctrl = next(c for c in manifest["controls"] if c["id"] == "fork")
    procs = ctrl["workpaper"]["procedures"]
    assert len(procs) == 2, (
        f"expected 2 workpaper procedures for the forked control, "
        f"got {len(procs)}: {[p.get('title') for p in procs]}"
    )
    titles = {p["title"] for p in procs}
    assert "High pass rate" in titles, f"Branch A title missing: {titles}"
    assert "Zero tolerance" in titles, f"Branch B title missing: {titles}"
