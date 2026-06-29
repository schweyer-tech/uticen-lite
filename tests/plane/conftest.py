from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uticen_lite.plane.app import create_app
from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.migrations import migrate


@pytest.fixture
def engagement(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    conn = connect(tmp_path)
    migrate(conn)
    repo.upsert_project(conn, name="Acme")
    conn.close()
    return tmp_path


@pytest.fixture
def client(engagement: Path) -> TestClient:
    return TestClient(create_app(engagement))
