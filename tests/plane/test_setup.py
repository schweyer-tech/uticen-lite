from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from controlflow_sdk.plane.app import create_app
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect


@pytest.fixture
def fresh_client(tmp_path: Path) -> TestClient:
    """A control plane on a brand-new engagement with no project name yet."""
    # create_app migrates and makes data/ — no project row is seeded.
    return TestClient(create_app(tmp_path))


def test_first_run_shows_setup_screen(fresh_client: TestClient):
    resp = fresh_client.get("/")
    assert resp.status_code == 200
    assert "Welcome to the Control Plane" in resp.text
    assert "Load the Northwind demo" in resp.text
    # No controls dashboard on first run.
    assert "New control" not in resp.text


def test_setup_layout_balances_the_two_cards(fresh_client: TestClient):
    """U7: the onboarding cards are equal-height with bottom-aligned CTAs, and the
    page is centered so it doesn't strand empty space below the fold.

    The polish is structural (wrapper + per-card body + pinned action row), so assert
    the scaffolding is present rather than pixel-measuring.
    """
    page = fresh_client.get("/").text
    # centering wrapper + balanced grid
    assert "setup-page" in page
    assert "setup-grid" in page
    # each card uses the equal-height body + a pinned actions row for its CTA
    assert page.count("setup-card-body") == 2
    assert page.count("setup-actions") == 2
    # the demo card now earns its height with concrete value points (no dead space)
    assert "setup-points" in page


def test_header_shows_no_engagement_chip_before_naming(fresh_client: TestClient):
    # The header renders the engagement name in a .chip once set; on first run there is
    # no name, so no empty chip (and no nav-to-nowhere) should appear.
    resp = fresh_client.get("/")
    assert 'class="chip"' not in resp.text
    assert 'class="app-nav"' not in resp.text


def test_post_setup_names_engagement_and_shows_dashboard(fresh_client: TestClient, tmp_path: Path):
    resp = fresh_client.post(
        "/setup",
        data={"name": "Acme FY26", "framework": "NIST SP 800-53"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    conn = connect(tmp_path)
    project = repo.get_project(conn)
    assert project["name"] == "Acme FY26"
    assert project["framework"] == "NIST SP 800-53"

    # The dashboard now renders, with the engagement name in the header.
    page = fresh_client.get("/")
    assert "Acme FY26" in page.text
    assert "New control" in page.text


def test_post_setup_blank_name_stays_on_setup(fresh_client: TestClient, tmp_path: Path):
    resp = fresh_client.post("/setup", data={"name": "   "}, follow_redirects=False)
    assert resp.status_code == 303
    assert repo.get_project(connect(tmp_path)) is None
    assert "Welcome to the Control Plane" in fresh_client.get("/").text


def test_post_setup_demo_loads_runnable_engagement(fresh_client: TestClient, tmp_path: Path):
    resp = fresh_client.post("/setup/demo", follow_redirects=False)
    assert resp.status_code == 303

    conn = connect(tmp_path)
    assert repo.get_project(conn)["name"]  # demo names the engagement
    assert len(repo.list_controls(conn)) == 9
    assert len(list((tmp_path / "data").glob("*.csv"))) == 9

    # Dashboard now lists the demo controls and a run works.
    page = fresh_client.get("/")
    assert "New control" in page.text
    control_id = repo.list_controls(conn)[0]["id"]
    run = fresh_client.post(f"/controls/{control_id}/run", follow_redirects=False)
    assert run.status_code in (200, 303)
