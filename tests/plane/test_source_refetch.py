from __future__ import annotations

import json


def _opener(records):
    body = json.dumps(records).encode()

    def opener(req):
        return body, "application/json"

    return opener


def _create_url_source(client, records):
    client.app.state.fetch_opener = _opener(records)
    client.post(
        "/sources/from-url",
        data={
            "source_id": "api",
            "url": "https://example.test/items.json",
            "headers": "",
            "record_path": "",
            "as_of_date": "2026-01-01",
        },
        follow_redirects=False,
    )


def test_refetch_stages_and_shows_diff(client):
    _create_url_source(client, [{"id": "A", "amt": 5}])
    # Remote now returns an extra column -> diff should surface it.
    client.app.state.fetch_opener = _opener([{"id": "A", "amt": 5, "note": "x"}])
    resp = client.post("/sources/api/refetch", follow_redirects=False)
    assert resp.status_code == 200
    assert "note" in resp.text  # added column shown in the diff
    assert "Confirm" in resp.text or "confirm" in resp.text


def test_refetch_without_url_source_redirects(client):
    # CSV source has no source_fetch row.
    import io

    client.post(
        "/sources",
        data={"source_id": "c", "as_of_date": "2026-01-01"},
        files={"file": ("c.csv", io.BytesIO(b"id\nA\n"), "text/csv")},
        follow_redirects=False,
    )
    resp = client.post("/sources/c/refetch", follow_redirects=False)
    assert resp.status_code in (302, 303)
