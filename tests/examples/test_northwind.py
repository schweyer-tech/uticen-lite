"""End-to-end fixture test for the Northwind Trading example.

Copies examples/northwind-trading into a tmp_path, drives the CLI in-process,
asserts the seeded exception counts, and verifies the built bundle is valid.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from controlflow_sdk.cli import main
from controlflow_sdk.runner.runlog import read_runs
from controlflow_sdk.schema.validate import validate_bundle

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
}


# ---------------------------------------------------------------------------
# Fixture test
# ---------------------------------------------------------------------------


def test_northwind_runs_and_builds(tmp_path: Path) -> None:
    """Full pipeline: validate → run → assert counts → build → validate_bundle."""
    proj = tmp_path / "northwind"
    shutil.copytree(EXAMPLE_DIR, proj)

    # 1. Validate ---------------------------------------------------------------
    assert main(["validate", str(proj)]) == 0, "cflow validate failed"

    # 2. Run at deterministic snapshot ------------------------------------------
    assert main(["run", str(proj), "--at", AT]) == 0, "cflow run failed"

    # 3. Assert per-control failed counts from the run log ----------------------
    runs = read_runs(proj / "target")
    assert runs, "run-log.json is empty after cflow run"

    by_control: dict[str, int] = {r["control_id"]: r["failed"] for r in runs}

    for cid, expected_n in EXPECTED.items():
        actual = by_control.get(cid)
        assert actual is not None, f"No run log entry found for control '{cid}'"
        assert actual == expected_n, (
            f"Control '{cid}': expected {expected_n} violation(s), got {actual}"
        )

    # Exactly the 8 expected controls — no extras, no missing.
    assert set(by_control.keys()) == set(EXPECTED.keys()), (
        f"Unexpected controls in run log: {set(by_control.keys()) ^ set(EXPECTED.keys())}"
    )

    # Total exceptions across the population are unchanged at 18.
    assert sum(by_control.values()) == 18, "Northwind seeded exception total drifted from 18"

    # 3b. Threshold flips a failing control to PASS -----------------------------
    # three-way-match has 4/30 exceptions (13.3%) but a 15% tolerance → PASSES.
    twm_html = (proj / "target" / "workpapers" / "Finance.AP.1.html").read_text(encoding="utf-8")
    body = twm_html[twm_html.index("</style>") :]
    assert "Operated effectively" in body, "three-way-match should pass under its 15% threshold"
    assert "within threshold" in body
    # A control with no threshold still fails on any exception (implicit-0).
    mjr_html = (proj / "target" / "workpapers" / "Finance.GL.1.html").read_text(encoding="utf-8")
    assert "Operated with deficiencies" in mjr_html
    assert "zero exceptions tolerated" in mjr_html

    # 4. Build bundle -----------------------------------------------------------
    out = proj / "bundle.zip"
    assert main(["build", str(proj), "--out", str(out), "--at", AT]) == 0, "cflow build failed"
    assert out.exists(), "bundle.zip was not created"

    # 5. Validate bundle manifest -----------------------------------------------
    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("manifest.json"))

    errors = validate_bundle(manifest)
    assert errors == [], f"validate_bundle reported errors: {errors}"
    assert len(manifest["controls"]) == 8, (
        f"Expected 8 controls in manifest, got {len(manifest['controls'])}"
    )
