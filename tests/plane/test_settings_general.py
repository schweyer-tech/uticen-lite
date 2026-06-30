"""Route tests for the general Settings section + engagement rename (U8).

Single-engagement by design (STRATEGY.md non-goal: not a platform) — these tests
cover only the landing page and renaming the *current* engagement, never
multi-engagement switching.
"""

from __future__ import annotations

from pathlib import Path

from uticen_lite.store import repo
from uticen_lite.store.db import connect


def test_settings_landing_is_not_ai_only(client):
    # The nav "Settings" link lands on a general section that fans out to the
    # engagement details and the AI sub-page — it is no longer AI-only.
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "Engagement" in resp.text
    # Links onward to the AI provider page.
    assert 'href="/settings/ai"' in resp.text
    # Carries a rename form prefilled with the current name.
    assert 'action="/settings/rename"' in resp.text
    assert "Acme" in resp.text


def test_nav_settings_points_at_landing_not_ai(client):
    # The header nav must point at /settings (the landing), not /settings/ai.
    home = client.get("/").text
    assert 'href="/settings"' in home
    assert 'href="/settings/ai">Settings' not in home


def test_rename_persists_to_project_record(client):
    resp = client.post("/settings/rename", data={"name": "Globex — FY27"}, follow_redirects=False)
    assert resp.status_code == 303
    conn = connect(client.app.state.project_root)
    project = repo.get_project(conn)
    conn.close()
    assert project["name"] == "Globex — FY27"
    # The new name surfaces in the header chip on the next page load.
    assert "Globex — FY27" in client.get("/").text


def test_rename_preserves_framework_and_ai_selection(client):
    # Seed a framework + an AI selection, then rename. The rename must touch ONLY
    # the name — framework and the store-only AI config must survive.
    root: Path = client.app.state.project_root
    conn = connect(root)
    repo.upsert_project(
        conn,
        name="Acme",
        framework="NIST SP 800-53",
        system={"ai": {"provider": "openai", "model": "gpt-4o"}},
        created_at="2026-01-01T00:00:00Z",
    )
    conn.close()

    client.post("/settings/rename", data={"name": "Acme Renamed"}, follow_redirects=False)

    conn = connect(root)
    project = repo.get_project(conn)
    conn.close()
    assert project["name"] == "Acme Renamed"
    assert project["framework"] == "NIST SP 800-53"
    assert project["system"]["ai"] == {"provider": "openai", "model": "gpt-4o"}


def test_blank_rename_is_a_no_op(client):
    # A blank name must never wipe the engagement name.
    client.post("/settings/rename", data={"name": "   "}, follow_redirects=False)
    conn = connect(client.app.state.project_root)
    project = repo.get_project(conn)
    conn.close()
    assert project["name"] == "Acme"
