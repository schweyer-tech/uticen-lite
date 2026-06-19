from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from controlflow_sdk.plane.app import create_app
from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect
from controlflow_sdk.store.migrations import migrate


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
