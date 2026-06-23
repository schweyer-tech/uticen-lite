from __future__ import annotations

import json

import pytest

from controlflow_sdk.plane import fetch


def _opener(body: bytes, ctype: str):
    captured = {}
    def opener(req):
        captured["headers"] = dict(req.header_items())
        captured["url"] = req.full_url
        return body, ctype
    opener.captured = captured
    return opener


def test_json_array_becomes_csv():
    body = json.dumps([{"id": "A", "amt": 5}, {"id": "B", "amt": 6}]).encode()
    snap = fetch.fetch_snapshot("https://x/items.json",
                                opener=_opener(body, "application/json"))
    assert snap.fmt == "csv"
    assert snap.raw.decode().splitlines()[0] == "id,amt"
    assert "A,5" in snap.raw.decode()
    assert snap.source_url == "https://x/items.json"


def test_record_path_navigates_and_headers_forwarded():
    body = json.dumps({"data": {"items": [{"id": "Z"}]}}).encode()
    op = _opener(body, "application/json")
    snap = fetch.fetch_snapshot("https://x/api", headers={"Authorization": "Bearer t"},
                                record_path="data.items", opener=op)
    assert "Z" in snap.raw.decode()
    # urllib title-cases header keys
    assert op.captured["headers"].get("Authorization") == "Bearer t"


def test_csv_passthrough():
    snap = fetch.fetch_snapshot("https://x/data.csv",
                                opener=_opener(b"id,n\nA,1\n", "text/csv"))
    assert snap.fmt == "csv" and snap.raw == b"id,n\nA,1\n"


def test_errors_are_fetcherror():
    with pytest.raises(fetch.FetchError):
        fetch.fetch_snapshot("ftp://nope", opener=_opener(b"", ""))
    with pytest.raises(fetch.FetchError):  # not a JSON array
        fetch.fetch_snapshot("https://x/o.json",
                             opener=_opener(b'{"a":1}', "application/json"))
    with pytest.raises(fetch.FetchError):  # bad record_path
        fetch.fetch_snapshot("https://x/o.json", record_path="missing",
                             opener=_opener(b'{"data":[]}', "application/json"))
