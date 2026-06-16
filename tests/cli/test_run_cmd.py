"""Tests for the ``cflow run`` subcommand."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from controlflow_sdk.cli import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_PROJECT = Path(__file__).parent.parent / "project" / "fixtures" / "sample"

FIXED_AT = "2026-06-16T00:00:00Z"

# The fixture control id (from controls/cash_cutoff/control.yaml).
CONTROL_ID = "cash_cutoff"


def _copy_project(src: Path, dest: Path) -> Path:
    """Recursively copy the fixture project into dest (excluding __pycache__)."""
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


# ---------------------------------------------------------------------------
# Happy-path: run all controls
# ---------------------------------------------------------------------------


class TestRunAll:
    def test_returns_0(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        rc = main(["run", str(proj), "--at", FIXED_AT])
        assert rc == 0

    def test_creates_markdown_workpaper(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        wp = proj / "target" / "workpapers" / f"{CONTROL_ID}.md"
        assert wp.exists(), f"Expected {wp} to be created"
        assert wp.stat().st_size > 0

    def test_creates_html_workpaper(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        wp = proj / "target" / "workpapers" / f"{CONTROL_ID}.html"
        assert wp.exists(), f"Expected {wp} to be created"
        assert wp.stat().st_size > 0

    def test_html_starts_with_doctype(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        html = (proj / "target" / "workpapers" / f"{CONTROL_ID}.html").read_text()
        assert html.strip().lower().startswith("<!doctype html>")

    def test_creates_violations_json(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        ev = proj / "target" / "evidence" / f"{CONTROL_ID}-violations.json"
        assert ev.exists(), f"Expected {ev} to be created"

    def test_violations_json_is_list(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        ev = proj / "target" / "evidence" / f"{CONTROL_ID}-violations.json"
        data = json.loads(ev.read_text())
        assert isinstance(data, list)

    def test_creates_run_log(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        log = proj / "target" / "run-log.json"
        assert log.exists(), "Expected target/run-log.json to be created"

    def test_run_log_is_non_empty(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        log = proj / "target" / "run-log.json"
        assert log.stat().st_size > 0

    def test_run_log_contains_control_id(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        log = proj / "target" / "run-log.json"
        line = log.read_text().strip().splitlines()[0]
        record = json.loads(line)
        assert record["control_id"] == CONTROL_ID

    def test_run_log_contains_executed_at(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        log = proj / "target" / "run-log.json"
        line = log.read_text().strip().splitlines()[0]
        record = json.loads(line)
        assert record["executed_at"] == FIXED_AT

    def test_prints_summary_line(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--at", FIXED_AT])
        out = capsys.readouterr().out
        # Summary line must mention the control id
        assert CONTROL_ID in out


# ---------------------------------------------------------------------------
# --control filter
# ---------------------------------------------------------------------------


class TestRunSingleControl:
    def test_returns_0_for_known_control(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        rc = main(["run", str(proj), "--control", CONTROL_ID, "--at", FIXED_AT])
        assert rc == 0

    def test_creates_workpaper_for_selected_control(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--control", CONTROL_ID, "--at", FIXED_AT])
        wp = proj / "target" / "workpapers" / f"{CONTROL_ID}.md"
        assert wp.exists()

    def test_returns_1_for_unknown_control(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        rc = main(["run", str(proj), "--control", "does_not_exist", "--at", FIXED_AT])
        assert rc == 1

    def test_unknown_control_prints_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj), "--control", "does_not_exist", "--at", FIXED_AT])
        out = capsys.readouterr()
        combined = out.out + out.err
        assert "does_not_exist" in combined


# ---------------------------------------------------------------------------
# --at default (clock boundary)
# ---------------------------------------------------------------------------


class TestAtDefault:
    def test_run_without_at_still_exits_0(self, tmp_path: Path) -> None:
        """--at is optional; the CLI injects now() when omitted."""
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        rc = main(["run", str(proj)])
        assert rc == 0

    def test_run_without_at_creates_workpaper(self, tmp_path: Path) -> None:
        proj = _copy_project(SAMPLE_PROJECT, tmp_path / "proj")
        main(["run", str(proj)])
        wp = proj / "target" / "workpapers" / f"{CONTROL_ID}.md"
        assert wp.exists()
