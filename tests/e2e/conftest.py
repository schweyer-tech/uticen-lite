"""Fixtures for the control-plane browser smoke test (issue #13).

This package is excluded from the fast unit lane via ``addopts =
"--ignore=tests/e2e"`` in pyproject, so importing ``uvicorn`` / Playwright here
never touches ``pytest -q``. The CI ``e2e`` job runs it explicitly.

The live server runs **in-process on a background thread** (not a subprocess):
``create_app(project_root)`` builds the full app from a ``Path``, and the
handlers open their own sqlite connection per request (learning 0002), so no
connection ever crosses a thread — a threaded server is safe and lets us pick an
ephemeral port and tear down deterministically.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from controlflow_sdk.plane.app import create_app
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.migrations import migrate


def _free_port() -> int:
    """Return an OS-assigned free TCP port on the loopback interface."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
    return port


@pytest.fixture
def engagement(tmp_path: Path) -> Path:
    """A migrated temp engagement with a named project.

    Mirrors ``tests/plane/conftest.py``. The project name makes the dashboard
    render the real Controls page rather than the first-run ``setup.html``
    onboarding screen, keeping the flow deterministic.
    """
    (tmp_path / "data").mkdir(exist_ok=True)
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_project(conn, name="E2E Co")
    conn.close()
    return tmp_path


@pytest.fixture
def live_server(engagement: Path) -> Iterator[str]:
    """Launch ``controlplane`` on a free port via a threaded ``uvicorn.Server``.

    Yields the base URL. Polls ``server.started`` (uvicorn flips it once the
    socket is listening) with a deadline, then tears down with
    ``should_exit = True`` + ``thread.join``.
    """
    port = _free_port()
    app = create_app(engagement)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("controlplane did not start in time")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
