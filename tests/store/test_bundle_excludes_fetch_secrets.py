"""Guard: URL/credentials/sheet must NEVER enter the export bundle (cardinal rule).

STRATEGY.md "the one hard contract": secrets/sensitive state must never cross the
trust boundary into the export bundle.  The ``multi-format-sources`` feature added
a new class of store-only sensitive state:

- ``source_fetch`` table  — url + auth headers (e.g. Bearer tokens)
- ``sources.sheet``       — sheet name (can carry internal naming conventions)

This test seeds ALL of that state into the store, exports the bundle, and asserts
that none of the sensitive literals appear anywhere in the zip — across every
entry.  It is a GUARD / regression test: production code already excludes this
state, so it will pass green from day one; its job is to catch any future
regression that accidentally leaks it.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from uticen_lite.model.run import RunRecord, SourceProvenance
from uticen_lite.model.violation import Violation
from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.export_service import build_bundle
from uticen_lite.store.migrations import migrate

# --- distinctive literals so absence-assertions have real teeth ---------------
SECRET_TOKEN = "Bearer SUPERSECRET-TOKEN-zzz123"
SOURCE_URL = "https://internal.example.test/api/v1/users.json"
SHEET_NAME = "SecretSheetName-Q4"


# --- shared helpers -----------------------------------------------------------

def _engagement(tmp_path: Path):
    (tmp_path / "data").mkdir()
    (tmp_path / "target").mkdir()
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_project(conn, name="Gate Co")
    return conn


def _seed_source_with_fetch(conn) -> None:
    repo.upsert_source(
        conn, id="api", format="xlsx", path="data/api.xlsx",
        key_config={"mode": "single", "columns": ["user_id"]},
        sheet=SHEET_NAME,
    )
    repo.set_columns(conn, "api", [
        {"original_name": "user_id", "display_name": "user_id", "is_key": True},
        {"original_name": "role", "display_name": "role"},
    ])
    repo.upsert_source_fetch(
        conn, source_id="api", url=SOURCE_URL,
        headers={"Authorization": SECRET_TOKEN},
        record_path="data.users",
        last_fetched_at="20260622T000000Z",
    )


def _seed_rule_control_and_run(conn) -> None:
    repo.upsert_control(
        conn,
        repo.ControlRow(
            id="c1", title="Role check", objective="o", narrative="n",
            framework_refs={"nist": []}, test_kind="rule",
            rule_spec={
                "logic": "all",
                "conditions": [{"column": "role", "op": "not_empty"}],
                "severity": "low",
                "description_template": "",
                "item_key_column": "user_id",
            },
        ),
    )
    repo.set_control_sources(conn, "c1", ["api"])
    run = RunRecord(
        control_id="c1",
        executed_at="2026-06-22T00:00:00+00:00",
        population_size=1,
        violations=[Violation.from_raw(
            {"item_key": "U1", "description": "x", "severity": "low", "details": {}},
        )],
        provenance=[SourceProvenance(
            source_id="api", path="data/api.xlsx", sha256="", row_count=1,
        )],
    )
    repo.insert_run(conn, run)


# --- the guard test -----------------------------------------------------------

def test_bundle_excludes_fetch_secrets(tmp_path: Path) -> None:
    """Export bundle must contain NO trace of URL, auth headers, or sheet name."""
    conn = _engagement(tmp_path)
    try:
        _seed_source_with_fetch(conn)
        _seed_rule_control_and_run(conn)

        # Teeth check: confirm the store actually HOLDS the sensitive data
        # (so the exclusion assertion below is not vacuously true).
        fetch = repo.get_source_fetch(conn, "api")
        assert fetch is not None, "source_fetch row should exist"
        assert fetch["headers"]["Authorization"] == SECRET_TOKEN, (
            "store must hold the auth token before we can verify the bundle omits it"
        )
        assert fetch["url"] == SOURCE_URL, "store must hold the URL"
        src = repo.get_source(conn, "api")
        assert src is not None, "source row should exist"
        assert src["sheet"] == SHEET_NAME, "store must hold the sheet name"

        # Export the bundle.
        out = build_bundle(
            conn, tmp_path, tmp_path / "out.zip", "2026-06-22T00:00:00+00:00",
        )
        assert out.exists(), "build_bundle must produce a zip"

        # Read every byte of every zip entry into one blob.
        blob = ""
        with zipfile.ZipFile(out) as zf:
            for name in zf.namelist():
                blob += zf.read(name).decode("utf-8", errors="ignore")

        # None of the sensitive literals may appear anywhere in the bundle.
        needles = (
            SECRET_TOKEN,
            "SUPERSECRET",   # substring guard — catches partial leaks
            SOURCE_URL,
            "internal.example.test",  # hostname guard
            SHEET_NAME,
        )
        for needle in needles:
            assert needle not in blob, (
                f"{needle!r} leaked into the export bundle — cardinal rule violated"
            )
    finally:
        conn.close()
