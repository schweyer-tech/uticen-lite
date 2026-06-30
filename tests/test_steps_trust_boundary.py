"""Bundle trust-boundary teeth-check (cardinal rule 0001 / learning 0026).

The step-inspection and export surfaces added in this branch (step data
inspector, per-step .xlsx export, full-pipeline workbook) must never leak
raw population data into the bundle.  This test verifies that guarantee
with a SENTINEL value:

- The source CSV carries an *extra* column (``extra_note``) whose value
  ``RAWLEAK_SENTINEL_7Q`` is intentionally unique and never referenced by
  the control's logic (the pipeline only touches ``user_id`` and
  ``can_create``).
- After a run + export the sentinel must appear in ZERO bytes of every
  entry in the bundle zip.

If a future change serialises raw rows into the bundle (e.g. embedding
the full frame in the manifest or a side-car file), this test fails — that
is the *teeth* of this guard.  Do not weaken it; fix the leak instead.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uticen_lite.plane.app import create_app
from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.migrations import migrate

# ---------------------------------------------------------------------------
# Fixtures — mirrors tests/plane/conftest.py (client not auto-discovered from
# that subdirectory when the test lives at tests/ root level).
# ---------------------------------------------------------------------------


@pytest.fixture()
def engagement(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_project(conn, name="Acme")
    conn.close()
    return tmp_path


@pytest.fixture()
def client(engagement: Path) -> TestClient:
    return TestClient(create_app(engagement))


# Sentinel value that appears only in the raw population, never in control
# logic or its compiled output.  If it appears anywhere in the bundle, raw
# data has leaked (cardinal rule 0001).
_SENTINEL = "RAWLEAK_SENTINEL_7Q"

# Source CSV: ``extra_note`` carries the sentinel; the pipeline only tests
# ``user_id`` / ``can_create``, so the sentinel value is NEVER referenced by
# the compiled test_code or the run result.
_CSV = (f"user_id,can_create,extra_note\nU1,true,{_SENTINEL}\nU2,false,{_SENTINEL}\n").encode()


def _seed_and_export(client):
    """Seed a source + control with a sentinel-bearing extra column, run,
    export, and return the raw zip bytes."""
    client.post(
        "/sources",
        data={"source_id": "users", "format": "csv"},
        files={"file": ("users.csv", io.BytesIO(_CSV), "text/csv")},
        follow_redirects=False,
    )
    # Exclude extra_note from the workpaper data sample: the column is present
    # in the raw population (so the step inspector can see it) but the include
    # flag controls whether it enters the workpaper's DataSample — omitting
    # ``include__extra_note`` from the form marks it as excluded.  Only
    # user_id / can_create are included; the sentinel never enters the workpaper
    # via the normal data-sample path, so any appearance in the bundle
    # indicates a NEW leak path (the teeth of this test).
    client.post(
        "/sources/users",
        data={
            "key_columns": "user_id",
            "include__user_id": "on",
            "include__can_create": "on",
            # ``include__extra_note`` is intentionally absent → excluded.
        },
        follow_redirects=False,
    )
    client.post(
        "/controls",
        data={
            "id": "boundary",
            "title": "Trust boundary check",
            "objective": "o",
            "narrative": "n",
            "source_ids": ["users"],
            "failure_threshold_count": "0",
        },
        follow_redirects=False,
    )
    # Pipeline only references user_id / can_create — extra_note is never touched.
    graph = {
        "nodes": [
            {"id": "imp", "type": "import", "source_id": "users"},
            {
                "id": "tst",
                "type": "test",
                "inputs": ["imp"],
                "config": {
                    "logic": "all",
                    "severity": "high",
                    "item_key_column": "user_id",
                    "description_template": "User {user_id}",
                    "conditions": [{"column": "can_create", "op": "eq", "value": "true"}],
                },
            },
        ]
    }
    client.post(
        "/controls/boundary/logic/builder",
        data={"pipeline_json": json.dumps(graph)},
        follow_redirects=False,
    )
    client.post("/controls/boundary/run", follow_redirects=False)
    resp = client.post("/export")
    return resp


def test_bundle_carries_no_raw_population_sentinel(client):
    """No entry in the exported bundle zip may contain the raw-population sentinel.

    This is the cardinal-rule-0001 teeth-check for the step-inspection branch:
    if ``extra_note``'s sentinel value were leaked (e.g. a future change
    serialised raw rows into the bundle), this test fails — that is intentional.
    """
    resp = _seed_and_export(client)
    assert resp.status_code == 200, f"Export failed: {resp.status_code} {resp.text[:200]}"

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        # Sanity: the bundle is real (not empty/broken); a hollow bundle
        # cannot pass vacuously.
        assert "manifest.json" in names, f"manifest.json missing from bundle; entries: {names}"
        manifest = json.loads(zf.read("manifest.json"))
        assert any(c["id"] == "boundary" for c in manifest.get("controls", [])), (
            "Control 'boundary' not found in manifest — bundle may be empty"
        )

        # Core assertion: sentinel must not appear in ANY bundle entry.
        for name in names:
            content = zf.read(name)
            # Decode leniently so binary entries (if any) don't throw.
            text = content.decode("utf-8", errors="replace")
            assert _SENTINEL not in text, (
                f"Raw population sentinel {_SENTINEL!r} leaked into bundle entry {name!r}. "
                "A bundle surface is serialising raw data rows (cardinal rule 0001 violation)."
            )
