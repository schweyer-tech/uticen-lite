"""The control plane creates a request's sqlite connection in FastAPI's
dependency-setup threadpool task but executes the sync GET handler in a DIFFERENT
threadpool thread (proven under concurrency). With sqlite3's default
``check_same_thread=True`` that raised ``ProgrammingError`` and 500'd every GET
(the header update indicator most visibly, since it fires concurrently with each
page load). The connection must therefore tolerate create-in-one-thread,
use-in-another. See learning 0002.
"""

from __future__ import annotations

import threading
from pathlib import Path

from uticen_lite.store.db import connect
from uticen_lite.store.migrations import migrate


def test_connection_is_usable_from_a_different_thread(tmp_path: Path) -> None:
    # Created here (the "dependency setup" thread).
    conn = connect(tmp_path)
    migrate(conn)

    captured: dict[str, object] = {}

    def use_in_other_thread() -> None:
        # The "endpoint" thread: execute + close, as get_conn's handler/finally do.
        try:
            captured["row"] = conn.execute("SELECT 1").fetchone()
            conn.close()
        except Exception as exc:  # noqa: BLE001 - surface the cross-thread error
            captured["error"] = exc

    t = threading.Thread(target=use_in_other_thread)
    t.start()
    t.join()

    assert "error" not in captured, f"connection rejected cross-thread use: {captured.get('error')}"
    assert tuple(captured["row"]) == (1,)
