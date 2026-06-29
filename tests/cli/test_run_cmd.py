"""Tests for the ``uticen-lite run`` subcommand (store-backed)."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from uticen_lite.cli import main
from uticen_lite.cli.import_cmd import import_cmd
from uticen_lite.store import repo
from uticen_lite.store.db import connect
from uticen_lite.store.run_service import run_control_in_store as _real_run_control_in_store

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "northwind-trading"

FIXED_AT = "2026-03-31T00:00:00+00:00"

# A single well-known control from the northwind example.
CONTROL_ID = "Finance.GL.1"


def _engagement(tmp_path: Path) -> Path:
    """Import the northwind example into an engagement dir and copy data files."""
    into = tmp_path / "eng"
    import_cmd(argparse.Namespace(src=str(EXAMPLE_DIR), into=str(into)))
    shutil.copytree(str(EXAMPLE_DIR / "data"), str(into / "data"))
    return into


# ---------------------------------------------------------------------------
# Happy-path: run all controls
# ---------------------------------------------------------------------------


class TestRunAll:
    def test_returns_0(self, tmp_path: Path) -> None:
        root = _engagement(tmp_path)
        rc = main(["run", str(root), "--at", FIXED_AT])
        assert rc == 0

    def test_creates_markdown_workpaper(self, tmp_path: Path) -> None:
        root = _engagement(tmp_path)
        main(["run", str(root), "--at", FIXED_AT])
        wp = root / "target" / "workpapers" / f"{CONTROL_ID}.md"
        assert wp.exists(), f"Expected {wp} to be created"
        assert wp.stat().st_size > 0

    def test_creates_html_workpaper(self, tmp_path: Path) -> None:
        root = _engagement(tmp_path)
        main(["run", str(root), "--at", FIXED_AT])
        wp = root / "target" / "workpapers" / f"{CONTROL_ID}.html"
        assert wp.exists(), f"Expected {wp} to be created"
        assert wp.stat().st_size > 0

    def test_html_starts_with_doctype(self, tmp_path: Path) -> None:
        root = _engagement(tmp_path)
        main(["run", str(root), "--at", FIXED_AT])
        html = (root / "target" / "workpapers" / f"{CONTROL_ID}.html").read_text()
        assert html.strip().lower().startswith("<!doctype html>")

    def test_creates_violations_json(self, tmp_path: Path) -> None:
        root = _engagement(tmp_path)
        main(["run", str(root), "--at", FIXED_AT])
        ev = root / "target" / "evidence" / f"{CONTROL_ID}-violations.json"
        assert ev.exists(), f"Expected {ev} to be created"
        data = json.loads(ev.read_text())
        assert isinstance(data, list), f"violations.json must be a list, got {type(data)}"

    def test_runs_persisted_to_store(self, tmp_path: Path) -> None:
        """Runs must be written to the SQLite store, not a run-log.json."""
        root = _engagement(tmp_path)
        main(["run", str(root), "--at", FIXED_AT])
        conn = connect(root)
        runs = repo.list_runs_for(conn, CONTROL_ID)
        assert runs, "Expected at least one run in the store for CONTROL_ID"

    def test_store_run_has_correct_executed_at(self, tmp_path: Path) -> None:
        root = _engagement(tmp_path)
        main(["run", str(root), "--at", FIXED_AT])
        conn = connect(root)
        runs = repo.list_runs_for(conn, CONTROL_ID)
        assert runs[0]["executed_at"] == FIXED_AT

    def test_prints_summary_line(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        root = _engagement(tmp_path)
        main(["run", str(root), "--at", FIXED_AT])
        out = capsys.readouterr().out
        assert CONTROL_ID in out


# ---------------------------------------------------------------------------
# --control filter
# ---------------------------------------------------------------------------


class TestRunSingleControl:
    def test_returns_0_for_known_control(self, tmp_path: Path) -> None:
        root = _engagement(tmp_path)
        rc = main(["run", str(root), "--control", CONTROL_ID, "--at", FIXED_AT])
        assert rc == 0

    def test_creates_workpaper_for_selected_control(self, tmp_path: Path) -> None:
        root = _engagement(tmp_path)
        main(["run", str(root), "--control", CONTROL_ID, "--at", FIXED_AT])
        wp = root / "target" / "workpapers" / f"{CONTROL_ID}.md"
        assert wp.exists()

    def test_returns_1_for_unknown_control(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        root = _engagement(tmp_path)
        rc = main(["run", str(root), "--control", "does_not_exist", "--at", FIXED_AT])
        assert rc == 1

    def test_unknown_control_prints_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        root = _engagement(tmp_path)
        main(["run", str(root), "--control", "does_not_exist", "--at", FIXED_AT])
        out = capsys.readouterr()
        combined = out.out + out.err
        assert "does_not_exist" in combined


# ---------------------------------------------------------------------------
# Missing / empty engagement store → friendly error, non-zero exit
# ---------------------------------------------------------------------------


class TestMissingStore:
    def test_run_on_dir_without_store_exits_1(self, tmp_path: Path) -> None:
        """A directory that was never imported has no store — run must exit 1,
        not die with a cryptic 'no such table: project'."""
        empty = tmp_path / "no-store"
        empty.mkdir()
        rc = main(["run", str(empty), "--at", FIXED_AT])
        assert rc == 1

    def test_run_on_dir_without_store_prints_actionable_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """The error must name the dir and point the user at `uticen-lite import`,
        not leak the raw sqlite3 'no such table' text."""
        empty = tmp_path / "no-store"
        empty.mkdir()
        main(["run", str(empty), "--at", FIXED_AT])
        err = capsys.readouterr().err
        assert "No engagement store found" in err
        assert "uticen-lite import" in err
        assert "no such table" not in err


# ---------------------------------------------------------------------------
# --at default (clock boundary)
# ---------------------------------------------------------------------------


class TestAtDefault:
    def test_run_without_at_still_exits_0(self, tmp_path: Path) -> None:
        """--at is optional; the CLI injects now() when omitted."""
        root = _engagement(tmp_path)
        rc = main(["run", str(root)])
        assert rc == 0

    def test_run_without_at_creates_workpaper(self, tmp_path: Path) -> None:
        root = _engagement(tmp_path)
        main(["run", str(root)])
        wp = root / "target" / "workpapers" / f"{CONTROL_ID}.md"
        assert wp.exists()


# ---------------------------------------------------------------------------
# Partial-failure contract
# ---------------------------------------------------------------------------


class TestPartialFailure:
    def test_partial_failure_continues_and_exits_1(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """When one control raises, run_cmd must: continue all others, return 1,
        and print the failing control id to stderr."""
        root = _engagement(tmp_path)
        conn = connect(root)

        # Discover the full control list so we can pick one to fail.
        from uticen_lite.store.loader import load_project_from_store

        project = load_project_from_store(conn)
        all_ids = [c.id for c in project.controls]
        assert len(all_ids) >= 2, "Need at least 2 controls to test partial failure"
        failing_id = all_ids[0]

        call_log: list[str] = []

        def _patched(conn_, root_, control_id: str, executed_at: str):  # noqa: ANN001
            call_log.append(control_id)
            if control_id == failing_id:
                raise RuntimeError(f"injected failure for {control_id}")
            return _real_run_control_in_store(conn_, root_, control_id, executed_at)

        with patch(
            "uticen_lite.cli.run_cmd.run_control_in_store",
            side_effect=_patched,
        ):
            rc = main(["run", str(root), "--at", FIXED_AT])

        captured = capsys.readouterr()

        # Contract: exit code 1 on any error.
        assert rc == 1, f"Expected exit code 1, got {rc}"

        # Contract: every control was attempted (not short-circuited).
        assert set(call_log) == set(all_ids), (
            f"Expected all controls to be called; called={call_log}"
        )

        # Contract: the failing control id appears in stderr.
        assert failing_id in captured.err, (
            f"Expected '{failing_id}' in stderr; got: {captured.err!r}"
        )
