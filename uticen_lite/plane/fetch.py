"""One-time, user-initiated URL fetch that snapshots a response to bytes.

NOT a live connector (STRATEGY.md non-goal): a single GET on an explicit user
action; the caller writes the result to a local file that becomes the source of
truth. The ``opener`` is injectable so tests never touch the network.
"""

from __future__ import annotations

import csv as csvmod
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

Opener = Callable[[urllib.request.Request], tuple[bytes, str]]


class FetchError(Exception):
    """User-facing failure of a one-time URL fetch."""


@dataclass(frozen=True)
class FetchedSnapshot:
    raw: bytes
    fmt: str  # csv | xlsx | parquet
    suggested_name: str
    source_url: str
    fetched_at: str  # 20260622T101913Z


def _default_opener(req: urllib.request.Request) -> tuple[bytes, str]:
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 user-initiated
            return resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raise FetchError(f"Server returned HTTP {e.code} for {req.full_url}") from e
    except urllib.error.URLError as e:
        raise FetchError(f"Could not reach {req.full_url}: {e.reason}") from e


def _infer_fmt(url: str, content_type: str) -> str:
    ct = content_type.lower()
    low = url.lower().split("?")[0]
    if "json" in ct or low.endswith(".json"):
        return "json"
    if low.endswith(".xlsx") or "spreadsheetml" in ct:
        return "xlsx"
    if low.endswith(".parquet") or "parquet" in ct:
        return "parquet"
    return "csv"


def _name_stem(url: str) -> str:
    stem = PurePosixPath(urlparse(url).path).stem
    return stem or "snapshot"


def _dig(payload: Any, record_path: str | None) -> Any:
    if not record_path:
        return payload
    node = payload
    for part in record_path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise FetchError(f"record_path {record_path!r}: no key {part!r} in the response")
        node = node[part]
    return node


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"))
    return str(v)


def _json_to_csv(raw: bytes, record_path: str | None) -> bytes:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FetchError(f"Response is not valid JSON: {e}") from e
    records = _dig(payload, record_path)
    if not isinstance(records, list):
        where = f"at {record_path!r}" if record_path else "at the top level"
        raise FetchError(f"Expected a JSON array of records {where}")
    if not records:
        raise FetchError("JSON response contained zero records")
    header: list[str] = []
    for rec in records:
        if not isinstance(rec, dict):
            raise FetchError("Each JSON record must be an object")
        for k in rec:
            if k not in header:
                header.append(k)
    buf = io.StringIO()
    w = csvmod.writer(buf)
    w.writerow(header)
    for rec in records:
        w.writerow([_cell(rec.get(k)) for k in header])
    return buf.getvalue().encode("utf-8")


def fetch_snapshot(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    record_path: str | None = None,
    opener: Opener | None = None,
) -> FetchedSnapshot:
    """GET *url* once and snapshot the response (JSON normalised to CSV)."""
    if not url.lower().startswith(("http://", "https://")):
        raise FetchError("URL must start with http:// or https://")
    opener = opener or _default_opener
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    body, content_type = opener(req)
    fmt = _infer_fmt(url, content_type)
    fetched_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = _name_stem(url)
    if fmt == "json":
        return FetchedSnapshot(
            _json_to_csv(body, record_path), "csv", f"{stem}.csv", url, fetched_at
        )
    return FetchedSnapshot(body, fmt, f"{stem}.{fmt}", url, fetched_at)
