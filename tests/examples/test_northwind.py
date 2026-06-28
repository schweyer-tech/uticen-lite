"""End-to-end fixture test for the Northwind Trading example (store-backed).

Imports examples/northwind-trading into an engagement store, drives the CLI
in-process, asserts the seeded exception counts, and verifies the built bundle is valid.
"""

from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path

from controlflow_sdk.cli import main
from controlflow_sdk.cli.import_cmd import import_cmd
from controlflow_sdk.schema.validate import validate_bundle
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "northwind-trading"

AT = "2026-03-31T00:00:00Z"

# Exact expected failed-exception counts for each control at the snapshot date.
EXPECTED: dict[str, int] = {
    "Finance.GL.1": 3,
    "Finance.GL.2": 2,
    "Finance.AP.1": 4,
    "IT.AC.1": 3,
    "IT.AC.2": 2,
    "IT.AC.3": 0,
    "Finance.AP.3": 2,
    "Finance.AP.2": 2,
    "IT.ENV.1": 2,  # data-center temperature (public-API source): Singapore + Mumbai > 27°C
}


# ---------------------------------------------------------------------------
# Fixture test
# ---------------------------------------------------------------------------


def test_northwind_runs_and_builds(tmp_path: Path) -> None:
    """Full pipeline: import → run → assert counts → build → validate_bundle."""
    # 0. Import YAML project into engagement store ---------------------------
    into = tmp_path / "northwind"
    import_cmd(argparse.Namespace(src=str(EXAMPLE_DIR), into=str(into)))
    shutil.copytree(str(EXAMPLE_DIR / "data"), str(into / "data"))

    # 1. Run at deterministic snapshot ------------------------------------------
    assert main(["run", str(into), "--at", AT]) == 0, "cflow run failed"

    # 2. Assert per-control failed counts from the store -------------------------
    conn = connect(into)
    by_control: dict[str, int] = {}
    for cid in EXPECTED:
        runs = repo.list_runs_for(conn, cid)
        assert runs, f"No run found in store for control '{cid}'"
        # Multi-procedure controls persist per-procedure runs + a control-level
        # aggregate (empty procedure_id); pick the aggregate for the control's
        # headline failed count. Single-procedure controls have one run → fallback.
        agg = next((r for r in runs if not r.get("procedure_id")), runs[0])
        by_control[cid] = agg["failed"]

    for cid, expected_n in EXPECTED.items():
        actual = by_control.get(cid)
        assert actual is not None, f"No run entry found for control '{cid}'"
        assert actual == expected_n, (
            f"Control '{cid}': expected {expected_n} violation(s), got {actual}"
        )

    # Exactly the 9 expected controls.
    assert set(by_control.keys()) == set(EXPECTED.keys()), (
        f"Unexpected controls in run results: {set(by_control.keys()) ^ set(EXPECTED.keys())}"
    )

    # Total exceptions across the population are unchanged at 20.
    assert sum(by_control.values()) == 20, "Northwind seeded exception total drifted from 20"

    # 2b. Threshold flips a failing control to PASS ----------------------------
    twm_html = (into / "target" / "workpapers" / "Finance.AP.1.html").read_text(encoding="utf-8")
    body = twm_html[twm_html.index("</style>") :]
    assert "Operated effectively" in body, "three-way-match should pass under its 15% threshold"
    assert "within threshold" in body
    # A control with no threshold still fails on any exception (implicit-0).
    mjr_html = (into / "target" / "workpapers" / "Finance.GL.1.html").read_text(encoding="utf-8")
    assert "Operated with deficiencies" in mjr_html
    assert "zero exceptions tolerated" in mjr_html

    # 3. Build bundle -----------------------------------------------------------
    out = into / "bundle.zip"
    assert main(["build", str(into), "--out", str(out), "--at", AT]) == 0, "cflow build failed"
    assert out.exists(), "bundle.zip was not created"

    # 4. Validate bundle manifest -----------------------------------------------
    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("manifest.json"))

    errors = validate_bundle(manifest)
    assert errors == [], f"validate_bundle reported errors: {errors}"
    assert len(manifest["controls"]) == 9, (
        f"Expected 9 controls in manifest, got {len(manifest['controls'])}"
    )
    # Finance.GL.1 now fans out to 2 per-procedure runs + 1 aggregate = 3; the
    # other 8 controls persist 1 run each (learning 0035) → 11 total.
    assert sum(len(c["runs"]) for c in manifest["controls"]) == 11, (
        "Expected 11 run entries (Finance.GL.1 multi-procedure: 2 per-proc + 1 aggregate)"
    )
    # Finance.GL.1's workpaper now carries TWO procedures (P1 SoD + P2 authorization).
    gl1 = next(c for c in manifest["controls"] if c["id"] == "Finance.GL.1")
    gl1_procs = gl1["workpaper"]["procedures"]
    assert [p["code"] for p in gl1_procs] == ["P1", "P2"], gl1_procs
    assert {p["assertion"] for p in gl1_procs} == {
        "Segregation of duties", "Authorization / approval evidence"
    }, gl1_procs
    # Each procedure's distinct-items-examined population = 5 (the post-materiality set).
    for proc in gl1_procs:
        assert proc["result"]["total"] == 5, (
            f"Finance.GL.1 {proc['code']}: expected population (result.total) = 5, "
            f"got {proc['result']['total']}"
        )
    # P1 flags 2 self-review exceptions (JE-V01, JE-V03); P2 flags 1 missing-reviewer (JE-V02).
    p1 = next(p for p in gl1_procs if p["code"] == "P1")
    p2 = next(p for p in gl1_procs if p["code"] == "P2")
    assert p1["result"]["failed"] == 2, (
        f"Finance.GL.1 P1: expected 2 failed, got {p1['result']['failed']}"
    )
    assert p2["result"]["failed"] == 1, (
        f"Finance.GL.1 P2: expected 1 failed, got {p2['result']['failed']}"
    )
    # Each procedure carries its own authored narrative (shown in the procedure
    # header + workpaper), distinct from the per-step node narratives.
    assert p1["narrative"] == (
        "Independent review (segregation of duties): every material manual journal "
        "entry must be reviewed by someone other than its preparer. Flags entries a "
        "preparer reviewed themselves."
    ), p1["narrative"]
    assert p2["narrative"] == (
        "Authorization evidence: every material manual journal entry must have an "
        "independent reviewer recorded. Flags entries with no reviewer assigned."
    ), p2["narrative"]
