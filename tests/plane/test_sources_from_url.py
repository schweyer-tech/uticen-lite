from __future__ import annotations

import json

from uticen_lite.plane import fetch
from uticen_lite.store import repo
from uticen_lite.store.db import connect


def _fake_opener(payload):
    body = json.dumps(payload).encode()
    def opener(req):
        return body, "application/json"
    return opener


def test_from_url_page_keeps_global_nav_and_chip(client):
    """B1: the Fetch-from-URL tab must carry the same global header as every other
    page — nav links + the engagement chip — not a stripped header."""
    page = client.get("/sources/from-url")
    assert page.status_code == 200
    # the engagement chip (project name) and the top nav are present
    assert "Acme" in page.text
    assert 'href="/sources"' in page.text and 'href="/export"' in page.text
    # and the <title> keeps its engagement prefix, not a dangling "— Add source"
    assert "Acme — Add source" in page.text


def test_from_url_error_keeps_global_nav(client):
    """The header must also survive a validation error on the URL tab (B1)."""
    resp = client.post("/sources/from-url", data={
        "source_id": "api", "url": "not-a-url", "headers": "{bad json",
        "record_path": "", "as_of_date": "",
    }, follow_redirects=False)
    assert resp.status_code == 200  # re-renders the form with an error
    assert "Acme" in resp.text
    assert 'href="/sources"' in resp.text


def test_create_from_url_snapshots_and_stores_fetch(client):
    # Inject a fake opener so no network is touched.
    client.app.state.fetch_opener = _fake_opener([{"id": "A", "amt": 5},
                                                  {"id": "B", "amt": 6}])
    resp = client.post("/sources/from-url", data={
        "source_id": "api", "url": "https://example.test/items.json",
        "headers": "", "record_path": "", "as_of_date": "2026-01-01",
    }, follow_redirects=False)
    assert resp.status_code in (302, 303)

    edit = client.get("/sources/api")
    assert "id" in edit.text and "amt" in edit.text
    conn = connect(client.app.state.project_root)
    src = repo.get_source(conn, "api")
    fetch_row = repo.get_source_fetch(conn, "api")
    conn.close()
    assert src["format"] == "csv"          # JSON snapshotted to CSV
    assert fetch_row["url"] == "https://example.test/items.json"
    # snapshot file exists on disk
    assert (client.app.state.project_root / src["path"]).is_file()


def test_from_url_form_shows_secrets_warning(client):
    page = client.get("/sources/from-url")
    assert "plaintext" in page.text.lower()
    assert "controlplane.db" in page.text


def test_fetch_error_rerenders_form(client):
    def boom(req):
        raise fetch.FetchError("Could not reach host")
    client.app.state.fetch_opener = boom
    resp = client.post("/sources/from-url", data={
        "source_id": "bad", "url": "https://nope.test/x.json",
        "headers": "", "record_path": "", "as_of_date": "",
    }, follow_redirects=False)
    assert resp.status_code == 200
    assert "Could not reach host" in resp.text
